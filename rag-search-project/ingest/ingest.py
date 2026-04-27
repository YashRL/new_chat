import os
import re
import time
import json
import uuid
import shutil
import random
import hashlib
import logging
import gzip
import unicodedata
import datetime
import tiktoken
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from typing import List, Dict, Optional, Tuple

from db.db import get_db_connection
from content_extraction.data_extractor import process_pdf
from embedding_client import create_embeddings as _create_embeddings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
BASE_TOKENS          = 500
MIN_TOKENS           = 200
MAX_TOKENS           = 800
OVERLAP              = 100
BATCH_SIZE           = 96
MAX_RETRIES          = 5

CHILD_MAX_TOKENS     = 128
CHILD_MIN_TOKENS     = 40
PARENT_MAX_TOKENS    = 512

enc = tiktoken.get_encoding("cl100k_base")


def clean_text(text: str) -> str:
    """Normalize text extracted from PDFs.

    - Strips null bytes and most C0/C1 control characters
    - Applies Unicode NFKC normalization (expands ligatures: ﬁ→fi, ﬀ→ff, etc.)
    - Fixes common PDF hyphenation artifacts (word- break → word)
    - Collapses repeated whitespace
    - Strips common page-number / running-header patterns
    """
    if not text:
        return ""
    # NFKC normalisation: expands ligatures and compatibility chars
    text = unicodedata.normalize("NFKC", text)
    # Remove null bytes and remaining C0/C1 control chars (keep \n\t)
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0B-\x1F\x7F]", "", text)
    # Fix soft hyphens / line-break hyphens ("word-\nbreak" → "wordbreak")
    text = re.sub(r"-\s*\n\s*", "", text)
    # Collapse horizontal whitespace
    text = re.sub(r"[ \t]+", " ", text)
    # Normalise paragraph breaks
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def clean_title(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\x00", "")
    text = re.sub(r"[\x00-\x1F\x7F]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip() or "Untitled"


def count_tokens(text: str) -> int:
    return len(enc.encode(text))


@lru_cache(maxsize=4096)
def _encode_cached(text: str) -> List[int]:
    """Token-encode with a bounded LRU cache (auto-evicts oldest entries)."""
    return enc.encode(text)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_into_sizes(paragraphs: List[str], max_tokens: int, min_tokens: int) -> List[str]:
    if not paragraphs:
        return []
    chunks = []
    current, current_tokens = [], 0
    for para in paragraphs:
        if not para.strip():
            continue
        tokens = len(_encode_cached(para))
        if current and current_tokens + tokens > max_tokens:
            chunks.append(" ".join(current))
            current = [para]
            current_tokens = tokens
        else:
            current.append(para)
            current_tokens += tokens
    if current:
        chunks.append(" ".join(current))
    merged = []
    for chunk in chunks:
        if merged and len(_encode_cached(chunk)) < min_tokens:
            merged[-1] += " " + chunk
        else:
            merged.append(chunk)
    return merged


def make_parent_child_chunks(paragraphs: List[str]) -> List[Dict]:
    parent_chunks = chunk_into_sizes(paragraphs, PARENT_MAX_TOKENS, CHILD_MIN_TOKENS)
    result = []
    for parent_text in parent_chunks:
        parent_paras = [p.strip() for p in re.split(r"\n\s*\n", parent_text) if p.strip()] or [parent_text]
        children = chunk_into_sizes(parent_paras, CHILD_MAX_TOKENS, CHILD_MIN_TOKENS)
        result.append({"parent": parent_text, "children": children})
    return result


def chunk_paragraphs(paragraphs: List[str]) -> List[str]:
    """Chunk paragraphs with sliding-window overlap (OVERLAP tokens from previous chunk prepended)."""
    if not paragraphs:
        return []

    chunks = []
    current, current_tokens = [], 0

    for para in paragraphs:
        if not para.strip():
            continue
        tokens = len(_encode_cached(para))
        if current and current_tokens + tokens > MAX_TOKENS:
            chunks.append(" ".join(current))
            current = [para]
            current_tokens = tokens
        else:
            current.append(para)
            current_tokens += tokens

    if current:
        chunks.append(" ".join(current))

    merged = []
    for chunk in chunks:
        if merged and len(_encode_cached(chunk)) < MIN_TOKENS:
            merged[-1] += " " + chunk
        else:
            merged.append(chunk)

    # Add sliding-window overlap: prepend tail of previous chunk
    final_chunks = []
    for i, chunk in enumerate(merged):
        if i == 0:
            final_chunks.append(chunk)
        else:
            prev_tokens = _encode_cached(merged[i - 1])[-OVERLAP:]
            curr_tokens = _encode_cached(chunk)
            combined = enc.decode(prev_tokens + curr_tokens)
            final_chunks.append(combined)
    return final_chunks


def _embed_with_retry(batch: List[str]) -> List[List[float]]:
    """Single-batch embedding with retry logic. Used by all three ingest modes."""
    for attempt in range(MAX_RETRIES):
        try:
            return _create_embeddings(batch)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                logger.error(f"Embedding batch failed after {MAX_RETRIES} retries: {e}")
                raise
            wait = (2 ** attempt) + random.uniform(0, 1) if "rate limit" in str(e).lower() else min(5 * (attempt + 1), 30)
            logger.warning(f"Embedding batch failed (attempt {attempt+1}/{MAX_RETRIES}): {e}. Retry in {wait:.1f}s...")
            time.sleep(wait)


def _resolve_existing_user_id(cur, user_id):
    if not user_id:
        return None
    cur.execute("SELECT id FROM users WHERE id = %s LIMIT 1", (str(user_id),))
    row = cur.fetchone()
    if row:
        return row[0]
    logger.warning("[INGEST] Ignoring unknown user id for audit field: %s", user_id)
    return None


def insert_document(cur, metadata: Dict, source_path: str, doc_type: str, keywords: List[str],
                    file_hash: str, created_by=None, updated_by=None,
                    visibility: Optional[Dict] = None,
                    canonical_doc_id=None, version_of=None) -> str:

    cur.execute("SELECT id FROM documents WHERE file_hash = %s LIMIT 1", (file_hash,))
    existing = cur.fetchone()
    if existing:
        logger.info(f"Document already exists with ID: {existing[0]}")
        return existing[0]

    created_by = _resolve_existing_user_id(cur, created_by)
    updated_by = _resolve_existing_user_id(cur, updated_by)

    cur.execute("""
        INSERT INTO documents (document_name, document_type, keywords, meta, file_hash,
                               created_by, updated_by, visibility, canonical_doc_id, version_of)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        clean_title(metadata.get("title", "")),
        doc_type,
        keywords,
        {**metadata, "total_tokens": metadata.get("total_tokens", 0)},
        file_hash,
        created_by,
        updated_by,
        visibility or {},
        canonical_doc_id,
        version_of
    ))
    doc_id = cur.fetchone()[0]
    logger.info(f"Created document with ID: {doc_id}")
    return doc_id


def insert_section(cur, document_id: str, title: str, parent_id=None, level=1, order_index=0) -> str:
    cur.execute("""
        INSERT INTO sections (document_id, parent_id, title, level, order_index)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (document_id, parent_id, clean_title(title), level, order_index))
    return cur.fetchone()[0]


def insert_chunk(cur, document_id: str, section_id: str, text: str, chunk_index: int,
                 page_number=None, parent_chunk_id=None, chunk_type: str = "standalone") -> str:
    text = clean_text(text)
    chunk_hash = hash_text(text)
    token_count = count_tokens(text)

    # Race-free upsert: if the same chunk_hash already exists in this document,
    # update its metadata; otherwise insert fresh.
    cur.execute("""
        INSERT INTO paragraphs
            (document_id, section_id, text, norm_text, token_count, paragraph_index,
             page_number, chunk_hash, parent_chunk_id, chunk_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (document_id, chunk_hash) DO UPDATE
            SET section_id       = EXCLUDED.section_id,
                parent_chunk_id  = EXCLUDED.parent_chunk_id,
                chunk_type       = EXCLUDED.chunk_type
        RETURNING id
    """, (document_id, section_id, text, text, token_count, chunk_index,
          page_number, chunk_hash, parent_chunk_id, chunk_type))
    return cur.fetchone()[0]


def insert_embedding(cur, paragraph_id: str, vector: List[float], model_name: str = None):
    if model_name is None:
        model_name = EMBEDDING_MODEL_NAME
    cur.execute("SELECT id FROM embeddings WHERE paragraph_id=%s", (paragraph_id,))
    existing = cur.fetchone()
    if existing:
        return existing[0]

    cur.execute("""
        INSERT INTO embeddings (paragraph_id, embedding, model_name)
        VALUES (%s, %s, %s)
        RETURNING id
    """, (paragraph_id, vector, model_name))
    emb_id = cur.fetchone()[0]
    cur.execute("UPDATE paragraphs SET embedding_id=%s WHERE id=%s", (emb_id, paragraph_id))
    return emb_id


def _prepare_page_chunks(args: Tuple) -> Tuple[int, str, List[Dict]]:
    """
    Worker function: given a page dict and its index, return
    (page_index, page_title, list_of_parent_child_dicts).
    Pure CPU work — no DB access, safe to run in threads.
    """
    idx, page = args
    paras = page.get("paragraphs") or []
    if "text" in page:
        raw_text = clean_text(page["text"])
        paras = [p.strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()]
    title = page.get("title") or page.get("label") or f"Page {idx}"
    if not paras:
        return idx, title, []
    return idx, title, make_parent_child_chunks(paras)


def collect_all_chunks(blocks: List[Dict]) -> List[str]:
    """
    Recursively walk ALL blocks and return a flat ordered list of every CHILD chunk text.
    Parent chunks are not embedded — only children are. Order must exactly match
    process_blocks_with_vectors traversal.
    """
    all_chunks = []
    for blk in blocks:
        paras = blk.get("paragraphs") or []
        if "text" in blk:
            raw_text = clean_text(blk["text"])
            paras = [p.strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()]
        if paras:
            for pc in make_parent_child_chunks(paras):
                all_chunks.extend(pc["children"])
        if blk.get("children"):
            all_chunks.extend(collect_all_chunks(blk["children"]))
    return all_chunks


def process_blocks_with_vectors(cur, blocks: List[Dict], document_id: str,
                                 all_vectors: List[List[float]], chunk_cursor: List[int],
                                 para_index: List[int], parent_id=None, level=1) -> int:
    """
    Traverse blocks in the same order as collect_all_chunks.
    For each section:
      - Insert parent chunks (type='parent', no embedding)
      - Insert child chunks (type='child', with embedding) linked to parent
    chunk_cursor[0] tracks position in all_vectors (child chunks only).
    para_index[0] tracks global paragraph_index across the whole document.
    """
    total = 0
    for idx, blk in enumerate(blocks):
        title = blk.get("title") or blk.get("label", f"Block {idx}")
        paras = blk.get("paragraphs") or []
        if "text" in blk:
            raw_text = clean_text(blk["text"])
            paras = [p.strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()]

        section_id = insert_section(cur, document_id, title, parent_id, level, idx)

        if paras:
            for pc in make_parent_child_chunks(paras):
                parent_id_chunk = insert_chunk(
                    cur, document_id, section_id,
                    pc["parent"], para_index[0],
                    chunk_type="parent"
                )
                para_index[0] += 1

                for child_text in pc["children"]:
                    child_id = insert_chunk(
                        cur, document_id, section_id,
                        child_text, para_index[0],
                        parent_chunk_id=parent_id_chunk,
                        chunk_type="child"
                    )
                    if child_id and chunk_cursor[0] < len(all_vectors):
                        insert_embedding(cur, child_id, all_vectors[chunk_cursor[0]])
                        total += 1
                    chunk_cursor[0] += 1
                    para_index[0] += 1

        if blk.get("children"):
            total += process_blocks_with_vectors(
                cur, blk["children"], document_id,
                all_vectors, chunk_cursor, para_index,
                parent_id=section_id, level=level + 1
            )

    return total


def ingest(file_path: str, keywords: List[str], doc_type: str, override_title: Optional[str] = None,
           created_by=None, updated_by=None, visibility: Optional[Dict] = None,
           force_skip_ocr: bool = False):

    doc_name = os.path.basename(file_path)
    logger.info(f"[INGEST] Starting pipeline for: {file_path}")

    original_base = os.path.splitext(os.path.basename(file_path))[0]
    safe_base = re.sub(r"[^\w\-]", "_", original_base)
    out_dir = f"{safe_base}_{uuid.uuid4().hex[:8]}_output"

    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "output.json")

    t_start = time.time()

    def _elapsed() -> str:
        s = int(time.time() - t_start)
        return f"{s // 60}m {s % 60}s"

    try:
        # ── STAGE 1: PDF Extraction ───────────────────────────────────────
        with tqdm(total=1, desc=f"[1/4] Extracting  {doc_name}", ncols=100, bar_format=
                  "{l_bar}{bar}| {elapsed} elapsed") as pbar:
            process_pdf(file_path, output_dir=out_dir, output_json="output.json",
                        min_toc_entries=10, force_skip_ocr=force_skip_ocr)
            pbar.update(1)

        if not os.path.exists(manifest_path):
            raise FileNotFoundError(f"Manifest not found at {manifest_path}")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        if not manifest:
            raise ValueError("Manifest is empty")

        metadata = manifest[0].get("metadata", {})
        if override_title:
            metadata["title"] = override_title

        content_entry = next((e for e in manifest if e.get("mode") in ["structured", "page_dump", "legacy_extraction"]), None)
        if not content_entry:
            raise ValueError("No valid content entry found in manifest")

        mode = content_entry.get("mode")

        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        file_hash = hashlib.sha256(raw_bytes).hexdigest()

        with get_db_connection() as conn:
            with conn.cursor() as cur:

                cur.execute("SELECT id FROM documents WHERE file_hash = %s LIMIT 1", (file_hash,))
                existing_doc = cur.fetchone()
                if existing_doc:
                    logger.info(f"[INGEST] Duplicate detected, returning existing document: {existing_doc[0]}")
                    return existing_doc[0]

                document_id = insert_document(
                    cur, metadata, file_path, doc_type, keywords, file_hash,
                    created_by=created_by, updated_by=updated_by, visibility=visibility
                )

                compressed_bytes = gzip.compress(raw_bytes)
                expiry_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)

                cur.execute("""
                    INSERT INTO document_files (document_id, file_data, filename, mime_type, expiry_date)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (document_id) DO UPDATE
                    SET file_data = EXCLUDED.file_data,
                        filename = EXCLUDED.filename,
                        mime_type = EXCLUDED.mime_type,
                        updated_at = now(),
                        expiry_date = EXCLUDED.expiry_date
                """, (document_id, compressed_bytes, os.path.basename(file_path), "application/pdf+gzip", expiry_date))

                chunk_ids = []
                chunks_texts = []

                if mode == "structured":
                    toc = content_entry.get("toc", [])

                    # ── STAGE 2: Chunking ─────────────────────────────────
                    with tqdm(total=1, desc=f"[2/4] Chunking    {doc_name}", ncols=100, bar_format=
                              "{l_bar}{bar}| {elapsed} elapsed") as pbar:
                        all_chunks = collect_all_chunks(toc)
                        pbar.update(1)

                    # ── STAGE 3: Embedding ────────────────────────────────
                    logger.info(f"[INGEST] Embedding {len(all_chunks)} chunks (structured mode)")
                    all_vectors = []
                    batches = [all_chunks[i:i + BATCH_SIZE] for i in range(0, len(all_chunks), BATCH_SIZE)]
                    with tqdm(total=len(batches), desc=f"[3/4] Embedding   {doc_name}", ncols=100,
                              unit="batch", bar_format="{l_bar}{bar}| batch {n_fmt}/{total_fmt} | {elapsed} elapsed") as pbar:
                        for batch in batches:
                            all_vectors.extend(_embed_with_retry(batch))
                            pbar.update(1)

                    # ── STAGE 4: Saving to DB ─────────────────────────────
                    with tqdm(total=len(toc), desc=f"[4/4] Saving      {doc_name}", ncols=100,
                              unit="section", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} sections | {elapsed} elapsed") as pbar:
                        chunk_cursor = [0]
                        para_index = [0]
                        for blk in toc:
                            process_blocks_with_vectors(
                                cur, [blk], document_id, all_vectors,
                                chunk_cursor, para_index
                            )
                            pbar.update(1)

                elif mode == "page_dump":
                    pages = content_entry.get("pages", [])

                    # ── STAGE 2: Parallel chunking of pages ───────────────
                    page_chunk_results: List[Tuple[int, str, List[Dict]]] = [None] * len(pages)
                    with tqdm(total=len(pages), desc=f"[2/4] Chunking    {doc_name}", ncols=100,
                              unit="page", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} pages | {elapsed} elapsed") as pbar:
                        with ThreadPoolExecutor(max_workers=4) as executor:
                            future_to_idx = {
                                executor.submit(_prepare_page_chunks, (idx, page)): idx
                                for idx, page in enumerate(pages)
                            }
                            for future in as_completed(future_to_idx):
                                idx, title, pc_list = future.result()
                                page_chunk_results[idx] = (idx, title, pc_list)
                                pbar.update(1)

                    # ── STAGE 3: Embedding all child chunks ───────────────
                    ordered_child_texts: List[str] = []
                    for _, _, pc_list in page_chunk_results:
                        for pc in pc_list:
                            ordered_child_texts.extend(pc["children"])

                    all_vectors = []
                    batches = [ordered_child_texts[i:i + BATCH_SIZE] for i in range(0, len(ordered_child_texts), BATCH_SIZE)]
                    with tqdm(total=len(batches), desc=f"[3/4] Embedding   {doc_name}", ncols=100,
                              unit="batch", bar_format="{l_bar}{bar}| batch {n_fmt}/{total_fmt} | {elapsed} elapsed") as pbar:
                        for batch in batches:
                            all_vectors.extend(_embed_with_retry(batch))
                            pbar.update(1)

                    # ── STAGE 4: DB inserts in order ──────────────────────
                    global_para_idx = 0
                    vec_cursor = 0
                    with tqdm(total=len(page_chunk_results), desc=f"[4/4] Saving      {doc_name}", ncols=100,
                              unit="page", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} pages | {elapsed} elapsed") as pbar:
                        for idx, title, pc_list in page_chunk_results:
                            if not pc_list:
                                pbar.update(1)
                                continue
                            section_id = insert_section(cur, document_id, title)
                            for pc in pc_list:
                                parent_id_chunk = insert_chunk(
                                    cur, document_id, section_id,
                                    pc["parent"], global_para_idx,
                                    page_number=idx, chunk_type="parent"
                                )
                                global_para_idx += 1
                                for child_text in pc["children"]:
                                    cid = insert_chunk(
                                        cur, document_id, section_id,
                                        child_text, global_para_idx,
                                        page_number=idx,
                                        parent_chunk_id=parent_id_chunk,
                                        chunk_type="child"
                                    )
                                    if cid and vec_cursor < len(all_vectors):
                                        insert_embedding(cur, cid, all_vectors[vec_cursor])
                                    vec_cursor += 1
                                    global_para_idx += 1
                            pbar.update(1)

                elif mode == "legacy_extraction":
                    paras = content_entry.get("paragraphs", [])
                    if paras:
                        # ── STAGE 2: Chunking ─────────────────────────────
                        with tqdm(total=1, desc=f"[2/4] Chunking    {doc_name}", ncols=100, bar_format=
                                  "{l_bar}{bar}| {elapsed} elapsed") as pbar:
                            pc_list = make_parent_child_chunks(paras)
                            pbar.update(1)

                        child_texts = [ct for pc in pc_list for ct in pc["children"]]

                        # ── STAGE 3: Embedding ────────────────────────────
                        all_vectors = []
                        batches = [child_texts[i:i + BATCH_SIZE] for i in range(0, len(child_texts), BATCH_SIZE)]
                        with tqdm(total=len(batches), desc=f"[3/4] Embedding   {doc_name}", ncols=100,
                                  unit="batch", bar_format="{l_bar}{bar}| batch {n_fmt}/{total_fmt} | {elapsed} elapsed") as pbar:
                            for batch in batches:
                                all_vectors.extend(_embed_with_retry(batch))
                                pbar.update(1)

                        # ── STAGE 4: Saving to DB ─────────────────────────
                        section_id = insert_section(cur, document_id, metadata.get("title", "Document"))
                        global_ci = 0
                        vec_cursor = 0
                        with tqdm(total=len(pc_list), desc=f"[4/4] Saving      {doc_name}", ncols=100,
                                  unit="chunk", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} chunks | {elapsed} elapsed") as pbar:
                            for pc in pc_list:
                                parent_id_chunk = insert_chunk(
                                    cur, document_id, section_id,
                                    pc["parent"], global_ci, chunk_type="parent"
                                )
                                global_ci += 1
                                for child_text in pc["children"]:
                                    cid = insert_chunk(
                                        cur, document_id, section_id,
                                        child_text, global_ci,
                                        parent_chunk_id=parent_id_chunk,
                                        chunk_type="child"
                                    )
                                    if cid and vec_cursor < len(all_vectors):
                                        insert_embedding(cur, cid, all_vectors[vec_cursor])
                                    vec_cursor += 1
                                    global_ci += 1
                                pbar.update(1)

                cur.execute(
                    "SELECT COUNT(*) FROM paragraphs WHERE document_id = %s AND chunk_type != 'parent'",
                    (document_id,)
                )
                db_count = cur.fetchone()[0]

                if db_count == 0:
                    conn.rollback()
                    raise ValueError(f"Ingestion produced zero paragraphs for document {document_id}")

                conn.commit()

        elapsed = _elapsed()
        logger.info(f"[INGEST] Complete. Document ID: {document_id}, paragraphs: {db_count}, elapsed: {elapsed}")
        tqdm.write(f"\n✅ Ingestion complete — {db_count} chunks saved in {elapsed}\n")
        return document_id

    except Exception:
        logger.exception(
            "[INGEST] Failed for file=%s created_by=%r updated_by=%r",
            file_path,
            created_by,
            updated_by,
        )
        raise
    finally:
        # The LRU cache evicts automatically; no manual clear needed.
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir, ignore_errors=True)
        # Remove the uploaded file after ingestion is complete
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

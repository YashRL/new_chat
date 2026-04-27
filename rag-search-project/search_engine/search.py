import logging
import math
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from db.db import run_query
from embedding_client import create_single_embedding as _create_single_embedding

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
CANDIDATE_POOL = 200
DEFAULT_TOP_K = 10
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() == "true"
HYDE_ENABLED = os.getenv("HYDE_ENABLED", "false").lower() == "true"
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
RERANK_MODEL = os.getenv("RERANK_MODEL", "nvidia/nv-rerankqa-mistral-4b-v3")

MAX_CHUNKS_PER_DOC = 2
MAX_CHUNKS_PER_SECTION = 2

FAIRNESS_LARGE_DOC_THRESHOLD = 200
FAIRNESS_SMALL_DOC_THRESHOLD = 20
FAIRNESS_LARGE_PENALTY = 0.04
FAIRNESS_SMALL_BONUS = 0.04

DOC_BEST_CHUNK_W = 0.70
DOC_SECOND_CHUNK_W = 0.20
DOC_TITLE_BOOST_W = 0.05
DOC_KW_BOOST_W = 0.05

WEIGHT_PROFILES = {
    "balanced": {"semantic": 0.50, "lexical": 0.30, "keywords": 0.10, "context": 0.10},
    "semantic": {"semantic": 0.70, "lexical": 0.15, "keywords": 0.10, "context": 0.05},
    "lexical": {"semantic": 0.20, "lexical": 0.60, "keywords": 0.15, "context": 0.05},
    "precise": {"semantic": 0.40, "lexical": 0.40, "keywords": 0.15, "context": 0.05},
}

PROFILE_ALIASES = {
    "semantic-heavy": "semantic",
    "lexical-heavy": "lexical",
    "auto": None,
}

COMPATIBLE_PROFILES = {
    "hybrid": ["auto", "balanced", "semantic-heavy", "lexical-heavy", "precise"],
    "semantic": ["auto", "semantic-heavy", "balanced"],
    "book": ["auto", "balanced", "semantic-heavy", "lexical-heavy", "precise"],
    "section": ["auto", "balanced", "semantic-heavy", "lexical-heavy", "precise"],
    "keywords": ["auto", "lexical-heavy", "balanced"],
}

SHORT_QUERY_THRESHOLD = 3
TECHNICAL_TERMS_INDICATOR = ["algorithm", "theorem", "definition", "proof", "formula"]
WORD_RE = re.compile(r"[a-z0-9]+")


def resolve_weights(
    search_type: str,
    weight_profile: Optional[str],
    query: Optional[str] = None,
) -> Dict[str, float]:
    profile = (weight_profile or "auto").strip().lower()
    canonical = PROFILE_ALIASES.get(profile, profile)
    allowed = COMPATIBLE_PROFILES.get(search_type, COMPATIBLE_PROFILES["hybrid"])
    if profile not in allowed and profile != "auto":
        logger.warning(
            "Incompatible weight profile '%s' for search_type='%s' — adjusted automatically to 'auto'",
            profile,
            search_type,
        )
        canonical = None
    if canonical is None:
        q = (query or "").lower()
        words = q.split()
        if any(t in q for t in TECHNICAL_TERMS_INDICATOR):
            canonical = "precise"
        elif 0 < len(words) <= SHORT_QUERY_THRESHOLD:
            canonical = "lexical"
        else:
            canonical = "balanced"
    return WEIGHT_PROFILES.get(canonical, WEIGHT_PROFILES["balanced"])


@lru_cache(maxsize=1000)
def embed_query(query_text: str) -> Tuple[float, ...]:
    return tuple(_create_single_embedding(query_text))


def classify_query(query: str) -> Dict[str, Any]:
    words = query.lower().split()
    return {
        "length": "short" if len(words) <= SHORT_QUERY_THRESHOLD else "long",
        "is_technical": any(term in query.lower() for term in TECHNICAL_TERMS_INDICATOR),
        "has_quotes": '"' in query,
        "is_question": query.strip().endswith("?"),
        "word_count": len(words),
    }


def generate_hypothetical_answer(query: str) -> str:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL)
        resp = client.chat.completions.create(
            model=os.getenv("HYDE_LLM_MODEL", "meta/llama-3.1-8b-instruct"),
            messages=[
                {
                    "role": "system",
                    "content": "Write a short, dense passage (2-3 sentences) that directly answers the following question. Write only the passage, no preamble.",
                },
                {"role": "user", "content": query},
            ],
            max_tokens=150,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("HyDE generation failed, falling back to raw query: %s", e)
        return query


@lru_cache(maxsize=200)
def embed_query_hyde(query_text: str) -> Tuple[float, ...]:
    if not HYDE_ENABLED:
        return embed_query(query_text)
    hypothetical = generate_hypothetical_answer(query_text)
    return tuple(_create_single_embedding(hypothetical))


def rerank_results(query: str, candidates: List[Dict], top_k: int) -> List[Dict]:
    if not RERANK_ENABLED or not candidates:
        return candidates[:top_k]
    try:
        from openai import OpenAI

        client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL)
        passages = [{"text": c.get("text", "")[:512]} for c in candidates]
        resp = client.rerank.create(
            model=RERANK_MODEL,
            query=query,
            documents=passages,
            top_n=top_k,
        )
        reranked = []
        for result in resp.results:
            candidate = candidates[result.index].copy()
            candidate["rerank_score"] = result.relevance_score
            candidate["score"] = result.relevance_score
            reranked.append(candidate)
        return reranked
    except Exception as e:
        logger.warning("Reranking failed, using original order: %s", e)
        return candidates[:top_k]


def _tokenize(text: Optional[str]) -> List[str]:
    return WORD_RE.findall((text or "").lower())


def _counter(text: Optional[str]) -> Counter:
    return Counter(_tokenize(text))


def _cosine_counter_similarity(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    overlap = sum(a[token] * b[token] for token in set(a) & set(b))
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return overlap / (mag_a * mag_b)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return max(0.0, min(dot / (mag_a * mag_b), 1.0))


def _fuzzy_similarity(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _lexical_score(query: Optional[str], text: Optional[str], title: Optional[str], section_title: Optional[str]) -> float:
    if not query:
        return 0.0
    query_counter = _counter(query)
    body_score = _cosine_counter_similarity(query_counter, _counter(text))
    title_score = _cosine_counter_similarity(query_counter, _counter(title))
    section_score = _cosine_counter_similarity(query_counter, _counter(section_title))
    fuzzy_score = max(_fuzzy_similarity(query, title), _fuzzy_similarity(query, section_title))
    return min(body_score * 0.7 + title_score * 0.15 + section_score * 0.1 + fuzzy_score * 0.05, 1.0)


def get_best_book_match(title_query: str) -> Optional[Tuple[str, str]]:
    rows = run_query("SELECT id, document_name FROM documents")
    scored = []
    for row in rows:
        name = row["document_name"]
        sim = max(_fuzzy_similarity(title_query, name), _lexical_score(title_query, name, name, None))
        if sim >= 0.2 or title_query.lower() in (name or "").lower():
            scored.append((sim, row["id"], name))
    if not scored:
        return None
    scored.sort(reverse=True)
    _, doc_id, doc_name = scored[0]
    return doc_id, doc_name


def get_best_section_match(section_query: str, document_id: Optional[str] = None) -> Optional[str]:
    if document_id:
        rows = run_query("SELECT id, title FROM sections WHERE document_id = %s", (document_id,))
    else:
        rows = run_query("SELECT id, title FROM sections", ())
    scored = []
    for row in rows:
        title = row["title"]
        sim = max(_fuzzy_similarity(section_query, title), _lexical_score(section_query, title, title, None))
        if sim >= 0.2 or section_query.lower() in (title or "").lower():
            scored.append((sim, row["id"]))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][1]


def _title_match_score(book_title: Optional[str], query: Optional[str]) -> float:
    if not book_title or not query:
        return 0.0
    title_words = set(_tokenize(book_title))
    query_words = set(_tokenize(query))
    if not query_words:
        return 0.0
    overlap = len(title_words & query_words)
    return min(overlap / len(query_words), 1.0)


def _keyword_match_score(doc_keywords: Optional[list], query_keywords: list) -> float:
    if not doc_keywords or not query_keywords:
        return 0.0
    doc_kw_set = {str(k).lower() for k in doc_keywords}
    matches = sum(1 for k in query_keywords if k.lower() in doc_kw_set)
    return min(matches / len(query_keywords), 1.0)


def _size_fairness_factor(doc_chunk_count: int) -> float:
    if doc_chunk_count <= FAIRNESS_SMALL_DOC_THRESHOLD:
        t = 1.0 - (doc_chunk_count / FAIRNESS_SMALL_DOC_THRESHOLD)
        return round(t * FAIRNESS_SMALL_BONUS, 5)
    if doc_chunk_count >= FAIRNESS_LARGE_DOC_THRESHOLD:
        excess = math.log1p(doc_chunk_count - FAIRNESS_LARGE_DOC_THRESHOLD)
        scale = math.log1p(1000)
        return round(-min(excess / scale, 1.0) * FAIRNESS_LARGE_PENALTY, 5)
    return 0.0


def _score_chunk(
    sem: float,
    lex: float,
    kw: float,
    section_id: Optional[str],
    section_hit_counts: Dict[str, int],
    weights: Dict[str, float],
    enable_context_boost: bool,
) -> Tuple[float, float]:
    context = 0.0
    if enable_context_boost and section_id:
        count = section_hit_counts.get(section_id, 0)
        context = min(count * 0.05, 0.15)

    chunk_score = (
        weights.get("semantic", 0.5) * sem
        + weights.get("lexical", 0.3) * lex
        + weights.get("keywords", 0.1) * kw
        + weights.get("context", 0.1) * context
    )
    return round(chunk_score, 6), round(context, 6)


def _score_document(
    chunk_scores: List[float],
    doc_chunk_count: int,
    title_boost: float,
    kw_boost: float,
) -> float:
    sorted_scores = sorted(chunk_scores, reverse=True)
    best = sorted_scores[0] if sorted_scores else 0.0
    second = sorted_scores[1] if len(sorted_scores) > 1 else 0.0

    base = (
        DOC_BEST_CHUNK_W * best
        + DOC_SECOND_CHUNK_W * second
        + DOC_TITLE_BOOST_W * title_boost
        + DOC_KW_BOOST_W * kw_boost
    )
    fairness = _size_fairness_factor(doc_chunk_count)
    return round(base + fairness, 6)


def _parse_keywords(raw_keywords: Any) -> List[str]:
    if isinstance(raw_keywords, list):
        return [str(k).strip().lower() for k in raw_keywords if str(k).strip()]
    if isinstance(raw_keywords, str) and raw_keywords.strip():
        return [k.strip().lower() for k in raw_keywords.split(",") if k.strip()]
    return []


def _parse_id_list(raw_ids: Any) -> List[str]:
    if not raw_ids:
        return []
    if isinstance(raw_ids, (list, tuple, set)):
        return [str(value).strip() for value in raw_ids if str(value).strip()]
    if isinstance(raw_ids, str):
        return [part.strip() for part in raw_ids.split(",") if part.strip()]
    return []


def _matches_keyword_filter(doc_keywords: Any, keywords_list: List[str]) -> bool:
    if not keywords_list:
        return True
    doc_kw = {str(k).lower() for k in (doc_keywords or [])}
    return any(k in doc_kw for k in keywords_list)


def hybrid_search(
    query: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    filters: Optional[Dict[str, Any]] = None,
    weights: Optional[Dict[str, float]] = None,
    weight_profile: Optional[str] = "auto",
    search_type: str = "hybrid",
    candidate_pool: int = CANDIDATE_POOL,
    enable_context_boost: bool = True,
    dedup_strategy: str = "hash",
    use_hyde: bool = False,
    use_rerank: bool = False,
) -> List[Dict[str, Any]]:
    if filters is None:
        filters = {}
    if weights is None:
        weights = resolve_weights(search_type, weight_profile, query)

    keywords_list = _parse_keywords(filters.get("keywords"))

    exact_doc_ids = _parse_id_list(filters.get("book_ids"))
    exact_doc_id = filters.get("book_id")
    if exact_doc_id and not exact_doc_ids:
        exact_doc_ids = [str(exact_doc_id).strip()]

    if not exact_doc_ids and filters.get("book"):
        match = get_best_book_match(filters["book"])
        if not match:
            logger.warning("No book match for '%s'", filters["book"])
            return []
        exact_doc_ids = [match[0]]

    exact_section_id = filters.get("section_id")
    section_doc_scope = exact_doc_ids[0] if len(exact_doc_ids) == 1 else None
    if not exact_section_id and filters.get("section"):
        exact_section_id = get_best_section_match(filters["section"], section_doc_scope)
        if filters.get("section") and not exact_section_id:
            return []

    where_clauses = ["p.chunk_type != 'parent'"]
    params: List[Any] = []

    if exact_doc_ids:
        if len(exact_doc_ids) == 1:
            where_clauses.append("d.id = %s")
            params.append(exact_doc_ids[0])
        else:
            placeholders = ", ".join(["%s"] * len(exact_doc_ids))
            where_clauses.append(f"d.id IN ({placeholders})")
            params.extend(exact_doc_ids)
    if exact_section_id:
        where_clauses.append("s.id = %s")
        params.append(exact_section_id)
    if filters.get("uploader_id"):
        where_clauses.append("d.created_by = %s")
        params.append(filters["uploader_id"])
    elif filters.get("uploader_email"):
        where_clauses.append("u.email = %s")
        params.append(filters["uploader_email"])
    if filters.get("created_after"):
        where_clauses.append("d.created_at >= %s")
        params.append(filters["created_after"])
    if filters.get("created_before"):
        where_clauses.append("d.created_at <= %s")
        params.append(filters["created_before"])
    if filters.get("min_tokens") is not None:
        where_clauses.append("p.token_count >= %s")
        params.append(filters["min_tokens"])
    if filters.get("max_tokens") is not None:
        where_clauses.append("p.token_count <= %s")
        params.append(filters["max_tokens"])

    rows = run_query(
        f"""
        SELECT
            p.id AS paragraph_id,
            p.text,
            p.token_count,
            p.chunk_hash,
            p.dup_cluster_id,
            p.paragraph_index,
            p.chunk_type,
            p.parent_chunk_id,
            parent_p.text AS parent_text,
            parent_p.token_count AS parent_token_count,
            d.id AS document_id,
            d.document_name AS book_title,
            d.keywords AS document_keywords,
            d.created_at AS document_created_at,
            d.total_tokens AS document_total_tokens,
            u.id AS uploader_id,
            u.display_name AS uploader_name,
            u.email AS uploader_email,
            s.id AS section_id,
            s.title AS section_title,
            s.level AS section_level,
            s.parent_id AS section_parent_id,
            e.embedding AS embedding
        FROM paragraphs p
        JOIN documents d ON p.document_id = d.id
        LEFT JOIN embeddings e ON e.paragraph_id = p.id
        LEFT JOIN users u ON d.created_by = u.id
        LEFT JOIN sections s ON p.section_id = s.id
        LEFT JOIN paragraphs parent_p ON parent_p.id = p.parent_chunk_id
        WHERE {' AND '.join(where_clauses)}
        """,
        tuple(params),
    )

    if not rows:
        return []

    doc_counts = Counter(row["document_id"] for row in rows)

    vector = None
    if query:
        try:
            vector = list(embed_query_hyde(query) if use_hyde and HYDE_ENABLED else embed_query(query))
        except Exception as e:
            logger.warning("Embedding failed: %s", e)

    candidates = []
    for row in rows:
        if not _matches_keyword_filter(row.get("document_keywords"), keywords_list):
            continue

        embedding = row.get("embedding") or []
        if isinstance(embedding, tuple):
            embedding = list(embedding)
        sem_score = _cosine_similarity(vector, embedding) if vector and embedding else 0.0
        lex_score = _lexical_score(query, row.get("text"), row.get("book_title"), row.get("section_title"))
        kw_match = 0
        if keywords_list:
            doc_kw = {str(k).lower() for k in (row.get("document_keywords") or [])}
            kw_match = sum(1 for k in keywords_list if k in doc_kw)

        raw_score = (
            weights.get("semantic", 0.5) * sem_score
            + weights.get("lexical", 0.3) * lex_score
            + weights.get("keywords", 0.1) * (kw_match / max(len(keywords_list), 1))
        )

        candidates.append(
            {
                **row,
                "sem_score": sem_score,
                "lex_score": lex_score,
                "kw_match": kw_match,
                "document_chunk_count": doc_counts.get(row["document_id"], 1),
                "_raw_score": raw_score,
            }
        )

    if not candidates:
        return []

    candidates.sort(key=lambda c: (c["_raw_score"], c["sem_score"], c["lex_score"]), reverse=True)
    candidates = candidates[: max(candidate_pool, top_k)]

    max_sem = max((c.get("sem_score") or 0.0) for c in candidates) or 1.0
    max_lex = max((c.get("lex_score") or 0.0) for c in candidates) or 1.0
    max_kw = max((c.get("kw_match") or 0) for c in candidates) or 1

    seen_hashes: set = set()
    section_hit_counts: Dict[str, int] = {}
    scored_chunks: List[Dict] = []

    for c in candidates:
        chunk_hash = c.get("chunk_hash")
        if dedup_strategy == "hash" and chunk_hash:
            if chunk_hash in seen_hashes:
                continue
            seen_hashes.add(chunk_hash)

        sem = float(c.get("sem_score") or 0.0) / max_sem
        lex = float(c.get("lex_score") or 0.0) / max_lex
        kw = float(c.get("kw_match") or 0.0) / max_kw
        section_id = c.get("section_id")

        chunk_score, ctx = _score_chunk(
            sem,
            lex,
            kw,
            section_id,
            section_hit_counts,
            weights,
            enable_context_boost,
        )

        if enable_context_boost and section_id:
            section_hit_counts[section_id] = section_hit_counts.get(section_id, 0) + 1

        scored_chunks.append(
            {
                "paragraph_id": c["paragraph_id"],
                "document_id": c.get("document_id"),
                "book_title": c.get("book_title"),
                "section_id": section_id,
                "section_title": c.get("section_title"),
                "section_level": c.get("section_level"),
                "text": c.get("text"),
                "token_count": c.get("token_count"),
                "paragraph_index": c.get("paragraph_index"),
                "chunk_type": c.get("chunk_type", "standalone"),
                "parent_context": {
                    "paragraph_id": str(c["parent_chunk_id"]) if c.get("parent_chunk_id") else None,
                    "text": c.get("parent_text"),
                    "token_count": c.get("parent_token_count"),
                }
                if c.get("parent_chunk_id")
                else None,
                "document_keywords": c.get("document_keywords"),
                "document_chunk_count": int(c.get("document_chunk_count") or 1),
                "uploaded_by": {
                    "id": c.get("uploader_id"),
                    "name": c.get("uploader_name") or c.get("uploader_email"),
                },
                "uploaded_at": c.get("document_created_at"),
                "chunk_hash": chunk_hash,
                "chunk_score": chunk_score,
                "_sem": round(sem, 4),
                "_lex": round(lex, 4),
                "_kw": round(kw, 4),
                "_ctx": round(ctx, 4),
            }
        )

    if not scored_chunks:
        return []

    doc_groups: Dict[str, Dict] = {}
    for ch in scored_chunks:
        doc_id = ch["document_id"]
        if doc_id not in doc_groups:
            doc_groups[doc_id] = {"doc_meta": ch, "chunks": []}
        doc_groups[doc_id]["chunks"].append(ch)

    doc_results: List[Dict] = []
    for doc_id, grp in doc_groups.items():
        meta = grp["doc_meta"]
        chunks = grp["chunks"]
        chunk_scores = [ch["chunk_score"] for ch in chunks]
        doc_chunk_count = meta["document_chunk_count"]
        title_boost = _title_match_score(meta["book_title"], query)
        kw_boost = _keyword_match_score(meta["document_keywords"], keywords_list)
        doc_score = _score_document(chunk_scores, doc_chunk_count, title_boost, kw_boost)
        doc_results.append(
            {
                "doc_id": doc_id,
                "doc_score": doc_score,
                "title_boost": round(title_boost, 4),
                "kw_boost": round(kw_boost, 4),
                "fairness": round(_size_fairness_factor(doc_chunk_count), 5),
                "chunks": chunks,
            }
        )

    doc_results.sort(key=lambda x: x["doc_score"], reverse=True)

    final_results: List[Dict] = []
    doc_chunk_counts_used: Dict[str, int] = {}
    section_chunk_counts_used: Dict[str, int] = {}

    for doc_entry in doc_results:
        if len(final_results) >= top_k:
            break

        doc_id = doc_entry["doc_id"]
        doc_score = doc_entry["doc_score"]
        chunks_sorted = sorted(doc_entry["chunks"], key=lambda x: x["chunk_score"], reverse=True)

        for ch in chunks_sorted:
            if len(final_results) >= top_k:
                break

            doc_used = doc_chunk_counts_used.get(doc_id, 0)
            if doc_used >= MAX_CHUNKS_PER_DOC:
                break

            sec_id = ch.get("section_id")
            sec_used = section_chunk_counts_used.get(sec_id, 0) if sec_id else 0
            if sec_id and sec_used >= MAX_CHUNKS_PER_SECTION:
                continue

            doc_chunk_counts_used[doc_id] = doc_used + 1
            if sec_id:
                section_chunk_counts_used[sec_id] = sec_used + 1

            final_results.append(
                {
                    "paragraph_id": ch["paragraph_id"],
                    "document_id": ch["document_id"],
                    "book_title": ch["book_title"],
                    "section_id": ch["section_id"],
                    "section_title": ch["section_title"],
                    "section_level": ch["section_level"],
                    "text": ch["text"],
                    "token_count": ch["token_count"],
                    "paragraph_index": ch["paragraph_index"],
                    "chunk_type": ch["chunk_type"],
                    "parent_context": ch["parent_context"],
                    "score": round(doc_score, 4),
                    "chunk_score": round(ch["chunk_score"], 4),
                    "score_breakdown": {
                        "semantic": ch["_sem"],
                        "lexical": ch["_lex"],
                        "keywords": ch["_kw"],
                        "context": ch["_ctx"],
                        "doc_score": round(doc_score, 4),
                        "title_boost": doc_entry["title_boost"],
                        "kw_boost": doc_entry["kw_boost"],
                        "fairness": doc_entry["fairness"],
                        "doc_chunks": ch["document_chunk_count"],
                    },
                    "weights_used": weights,
                    "document_keywords": ch["document_keywords"],
                    "uploaded_by": ch["uploaded_by"],
                    "uploaded_at": ch["uploaded_at"],
                    "chunk_hash": ch["chunk_hash"],
                }
            )

    if use_rerank and RERANK_ENABLED and query:
        final_results = rerank_results(query, final_results, top_k)

    return final_results


def semantic_search(query: str, top_k: int = 10):
    return hybrid_search(query=query, top_k=top_k, search_type="semantic", weight_profile="auto")


def search_by_book(book_query: str, limit: int = 50, topic: Optional[str] = None):
    return hybrid_search(query=topic, top_k=limit, filters={"book": book_query}, search_type="book", weight_profile="auto")


def search_by_section(section_query: str, limit: int = 20, book_query: Optional[str] = None):
    filters = {"section": section_query}
    if book_query:
        filters["book"] = book_query
    return hybrid_search(query=None, top_k=limit, filters=filters, search_type="section", weight_profile="auto")


def search_by_keywords(keyword_query: str, limit: int = 50):
    keywords = [k.strip().lower() for k in keyword_query.split(",") if k.strip()]
    return hybrid_search(
        query=None,
        top_k=limit,
        filters={"keywords": keywords},
        search_type="keywords",
        weight_profile="auto",
    )

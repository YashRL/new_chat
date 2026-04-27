# data_extractor.py

import fitz  # PyMuPDF
import json
import logging
import re
import os
import warnings
from pytesseract import TesseractNotFoundError
from tqdm import tqdm
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from content_extraction import ocr_utils

logger = logging.getLogger(__name__)


def ensure_dir(path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)


def extract_paragraphs(
    doc: fitz.Document,
    start_idx: int,
    end_idx: int,
    heading_text: str,
    override_texts: Optional[Dict[int, str]] = None
) -> List[str]:
    """Extract and split text by paragraphs between pages."""
    override_texts = override_texts or {}
    texts = []

    for pno in range(start_idx - 1, end_idx):
        if pno in override_texts:
            texts.append(override_texts[pno])
        else:
            page_text = doc[pno].get_text("text")
            if isinstance(page_text, str):
                texts.append(page_text)
            elif isinstance(page_text, dict):
                # Extract text if available
                text_content = page_text.get("text")
                if isinstance(text_content, str):
                    texts.append(text_content)
            # skip other types

    # Keep only strings and non-empty
    string_texts = [t for t in texts if isinstance(t, str) and t.strip()]
    full_text = "\n".join(string_texts)
    # Remove heading if present
    full_text = full_text.replace(heading_text, "", 1)
    # Split into paragraphs
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", full_text) if p.strip()]
    return paragraphs




def compute_spans(toc: List[tuple], total_pages: int) -> List[Dict[str, Any]]:
    """Compute page spans for TOC entries."""
    spans = []
    for i, (lvl, title, start) in enumerate(toc):
        end = total_pages
        for j in range(i + 1, len(toc)):
            if toc[j][0] <= lvl:
                end = toc[j][2] - 1
                break
        spans.append({"level": lvl, "title": title, "start": start, "end": end})
    return spans


def build_tree(spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build hierarchical tree from flat spans."""
    root = {"children": []}
    stack = [root]
    
    for entry in spans:
        lvl = entry["level"]
        node = {**entry, "children": []}
        
        while len(stack) > lvl:
            stack.pop()
        
        stack[-1]["children"].append(node)
        stack.append(node)
    
    return root["children"]


def node_to_dict(doc: fitz.Document, node: Dict[str, Any], output_dir: str, override_texts: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Convert tree node to dictionary with extracted content, supporting OCR override."""
    s, e = node["start"], node["end"]
    s_lbl = doc[s-1].get_label() or str(s)
    e_lbl = doc[e-1].get_label() or str(e)
    title = node["title"]
    
    paragraphs = extract_paragraphs(doc, s, e, title, override_texts)
    
    # Compute OCR confidence for this node if any page in range used OCR
    ocr_conf = None
    if override_texts:
        confs = [override_texts[p]["confidence"] for p in range(s-1, e) if p in override_texts]
        if confs:
            ocr_conf = sum(confs) / len(confs)
    
    node_dict = {
        "title": title,
        "level": node["level"],
        "span": f"{s_lbl}–{e_lbl}",
        "paragraphs": paragraphs,
        "children": [node_to_dict(doc, c, output_dir, override_texts) for c in node["children"]]
    }
    if ocr_conf is not None:
        node_dict["ocr_confidence"] = ocr_conf
    return node_dict


def fallback_per_page(doc: fitz.Document, output_dir: str, override_texts: Optional[Dict[int, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Extract content page by page when no TOC is available."""
    override_texts = override_texts or {}
    pages = []
    
    for idx in range(doc.page_count):
        lbl = doc[idx].get_label() or str(idx + 1)
        
        if idx in override_texts:
            page_data = override_texts[idx]
            pages.append({
                "label": lbl,
                "text": page_data["text"],
                "ocr_applied": True,
                "ocr_confidence": page_data["confidence"]
            })
        else:
            pages.append({
                "label": lbl,
                "text": doc[idx].get_text("text"),
                "ocr_applied": False
            })
            
    return pages


def process_pdf(pdf_path: str, output_dir: Optional[str] = None, output_json: str = "output.json",
                min_toc_entries: int = 10, force_skip_ocr: bool = False) -> None:
    if output_dir is None:
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        output_dir = base.replace(' ', '_')
    ensure_dir(output_dir)

    doc = fitz.open(pdf_path)
    metadata = doc.metadata
    ocr_dir = os.path.join(output_dir, "ocr")
    os.makedirs(ocr_dir, exist_ok=True)

    # Detect pages needing OCR (honours force_skip_ocr)
    pages_to_ocr = ocr_utils.detect_pages_needing_ocr(doc, force_skip=force_skip_ocr)
    override_texts = {}

    if pages_to_ocr and not ocr_utils.is_tesseract_available():
        logger.warning("Tesseract not available. Skipping OCR for %s", pdf_path)
        metadata["ocr_applied"] = False
        metadata["ocr_skipped_reason"] = "tesseract_not_available"
        pages_to_ocr = []

    if pages_to_ocr:
        metadata["ocr_applied"] = True
        metadata["ocr_page_count"] = len(pages_to_ocr)

        ocr_results = {}
        max_workers = min(4, len(pages_to_ocr))

        def ocr_one_page(page_num):
            text, conf = ocr_utils.process_page(doc, page_num, ocr_dir)
            return page_num, {"text": text, "confidence": conf}

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(ocr_one_page, p): p for p in pages_to_ocr}
                    for future in tqdm(as_completed(futures), total=len(futures), desc="Applying OCR (parallel)", ncols=100):
                        page_num, result = future.result()
                        ocr_results[page_num] = result
        except TesseractNotFoundError:
            logger.warning("Tesseract became unavailable during OCR. Continuing without OCR for %s", pdf_path)
            metadata["ocr_applied"] = False
            metadata["ocr_skipped_reason"] = "tesseract_not_available"
            metadata.pop("ocr_page_count", None)
            ocr_results = {}

        override_texts = ocr_results

    # TOC and content extraction
    raw_toc = doc.get_toc(simple=False)
    toc = [(lvl, title, page) for lvl, title, page, *_ in raw_toc]
    logger.debug("Found %d TOC entries (min_toc_entries=%d)", len(toc), min_toc_entries)

    if len(toc) < min_toc_entries:
        logger.info("Using page_dump mode for %s", pdf_path)
        content = {"mode": "page_dump", "pages": fallback_per_page(doc, output_dir, override_texts)}
    else:
        logger.info("Using structured mode for %s", pdf_path)
        spans = compute_spans(toc, doc.page_count)
        tree = build_tree(spans)
        # tqdm for structured nodes
        structured = [node_to_dict(doc, node, output_dir, override_texts) for node in tqdm(tree, desc="Processing TOC nodes", ncols=100)]
        content = {
            "mode": "structured",
            "toc": structured,
            "ocr_details": {"pages_processed": list(override_texts.keys()), "total_ocr_pages": len(override_texts)} if override_texts else None
        }

    output_list = [{"metadata": metadata}, content]
    out_path = os.path.join(output_dir, output_json)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_list, f, ensure_ascii=False, indent=2)
    print(f"Written → {out_path}")

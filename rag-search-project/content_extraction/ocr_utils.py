import os
from typing import Dict, List, Optional, Tuple
import pytesseract
from pytesseract import TesseractNotFoundError
import fitz  # PyMuPDF
import io
import numpy as np
import cv2
from PIL import Image, ImageOps
import json

# Configuration 
DEFAULT_DPI = 300
DEFAULT_LANG = 'eng'
DEFAULT_PSM = 3  # Default page segmentation mode
DEFAULT_OEM = 1  # LSTM OCR Engine Mode
MIN_CONFIDENCE = 50.0  # Minimum acceptable confidence score
TEXT_THRESHOLD = 500   # Minimum char length to skip OCR (was 200)
WORD_THRESHOLD = 50    # Minimum word count to skip OCR
IMAGE_THRESHOLD = 1  # Number of images that suggest a scanned page


def is_tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except (TesseractNotFoundError, FileNotFoundError, OSError):
        return False

def preprocess_for_ocr(pil_img: Image.Image, deskew: bool = False) -> Image.Image:
    """Preprocess image for better OCR results."""
    # Convert to grayscale
    img = ImageOps.grayscale(pil_img)
    arr = np.array(img)

    if deskew:
        # Compute skew angle and rotate if needed
        coords = np.column_stack(np.where(arr > 0))
        if coords.size:
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = -(90 + angle)
            else:
                angle = -angle
            (h, w) = arr.shape[:2]
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            arr = cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    # Apply denoising
    arr = cv2.medianBlur(arr, 3)
    
    # Adaptive thresholding for better text extraction
    arr = cv2.adaptiveThreshold(
        arr, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11, 2
    )
    
    return Image.fromarray(arr)

def render_page_to_pil(doc: fitz.Document, pno: int, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Render a PDF page to PIL Image."""
    page = doc[pno]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes("ppm")
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")

def ocr_image(img: Image.Image, lang: str = DEFAULT_LANG, psm: int = DEFAULT_PSM) -> Dict:
    """Perform OCR on a single image."""
    config = f'--oem {DEFAULT_OEM} --psm {psm}'
    
    # Get full text
    text = pytesseract.image_to_string(img, lang=lang, config=config)
    
    # Get detailed data including confidence scores
    data = pytesseract.image_to_data(img, lang=lang, config=config, output_type=pytesseract.Output.DICT)
    
    # Calculate confidence scores for non-empty words
    confs = [float(c) for c in data['conf'] if c != '-1']
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    
    return {
        "text": text,
        "confidence": avg_conf,
        "word_count": len([w for w in data['text'] if w.strip()]),
        "layout_data": data
    }

def detect_pages_needing_ocr(doc: fitz.Document, force_skip: bool = False) -> List[int]:
    """
    Detect which pages likely need OCR processing.

    A page is only sent to OCR when BOTH conditions hold:
      - fewer than TEXT_THRESHOLD characters  (500)
      - fewer than WORD_THRESHOLD words       (50)

    This avoids OCR on digital PDFs that happen to have a small number of
    characters but are clearly readable (e.g. diagrams with captions).

    Pass force_skip=True to skip OCR entirely for known-digital PDFs.
    """
    if force_skip:
        return []

    pages_to_ocr = []

    for idx in range(doc.page_count):
        page = doc[idx]
        text = page.get_text("text").strip()
        word_count = len(text.split())

        needs_ocr = (
            len(text) < TEXT_THRESHOLD and
            word_count < WORD_THRESHOLD
        )

        if needs_ocr:
            pages_to_ocr.append(idx)

    return pages_to_ocr

def process_page(doc: fitz.Document, pno: int, ocr_dir: str, dpi: int = DEFAULT_DPI, 
                lang: str = DEFAULT_LANG, psm: int = DEFAULT_PSM, 
                force: bool = False) -> Tuple[str, float]:
    """Process a single page, with caching."""
    cache_file = os.path.join(ocr_dir, f"page_{pno:04d}.json")
    
    # Check cache unless forced
    if not force and os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            cached = json.load(f)
            return cached['text'], cached['confidence']
    
    # Render and process
    img = render_page_to_pil(doc, pno, dpi=dpi)
    processed = preprocess_for_ocr(img)
    result = ocr_image(processed, lang=lang, psm=psm)
    
    # Cache result
    os.makedirs(ocr_dir, exist_ok=True)
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump({
            'text': result['text'],
            'confidence': result['confidence'],
            'word_count': result['word_count']
        }, f, ensure_ascii=False, indent=2)
    
    return result['text'], result['confidence']

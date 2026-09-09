"""
OCR fallback module for scanned and image-based invoice PDFs.
Renders pages using PyMuPDF and extracts text using Tesseract OCR.
"""

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text_via_ocr(pdf_bytes: bytes, max_pages: int = 3) -> str:
    """
    Renders PDF pages to images and runs optical character recognition (OCR).
    Limits page rendering to first max_pages to maintain V1 latency bounds.
    """
    if not pdf_bytes:
        return ""

    try:
        import fitz
        from PIL import Image
        import pytesseract
    except ImportError as e:
        logger.warning("OCR dependencies not available: %s", e)
        return ""

    extracted_pages = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = min(len(doc), max_pages)

        for page_idx in range(total_pages):
            page = doc[page_idx]
            # Render page at 150 DPI for balanced speed and recognition accuracy
            pix = page.get_pixmap(dpi=150)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            try:
                page_text = pytesseract.image_to_string(img)
                if page_text:
                    extracted_pages.append(page_text.strip())
            except Exception as ocr_err:
                logger.warning(
                    "Tesseract OCR failed on page %d (ensure tesseract binary is installed): %s",
                    page_idx, ocr_err,
                )

        doc.close()
        return "\n".join(extracted_pages).strip()

    except Exception as e:
        logger.error("Failed during PDF page rendering for OCR: %s", e)
        return ""

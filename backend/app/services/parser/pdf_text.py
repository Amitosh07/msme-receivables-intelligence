"""
Text-first extraction module for native digital PDF invoices.
Extracts text page-by-page using PyMuPDF (fitz) and pypdf fallback.
"""

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extracts text content from a digital PDF document.
    Returns concatenated text across all pages.
    """
    if not pdf_bytes or len(pdf_bytes) == 0:
        return ""

    text = _extract_via_fitz(pdf_bytes)
    if text and len(text.strip()) >= 20:
        return text

    # Fallback to pypdf if fitz returned little or no text
    fallback_text = _extract_via_pypdf(pdf_bytes)
    if len(fallback_text.strip()) > len(text.strip()):
        return fallback_text

    return text


def extract_layout_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract sorted text blocks, preserving lines and approximate reading order."""
    if not pdf_bytes:
        return ""
    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page in doc:
            blocks = page.get_text("blocks", sort=True)
            page_lines = [
                str(block[4]).strip()
                for block in blocks
                if len(block) > 4 and str(block[4]).strip()
            ]
            pages.append("\n".join(page_lines))
        doc.close()
        return "\n\n".join(pages).strip()
    except Exception as e:
        logger.debug("Layout-aware PDF extraction encountered error: %s", e)
        return ""


def _extract_via_fitz(pdf_bytes: bytes) -> str:
    """Extract text using PyMuPDF (fitz)."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_text = []
        for page in doc:
            pages_text.append(page.get_text("text"))
        doc.close()
        return "\n".join(pages_text).strip()
    except Exception as e:
        logger.debug("PyMuPDF text extraction encountered error: %s", e)
        return ""


def _extract_via_pypdf(pdf_bytes: bytes) -> str:
    """Extract text using pypdf."""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)
        return "\n".join(pages_text).strip()
    except Exception as e:
        logger.debug("pypdf text extraction encountered error: %s", e)
        return ""

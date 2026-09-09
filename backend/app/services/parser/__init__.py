"""
Invoice parsing package.
Exposes extraction services and structured models.
"""

from backend.app.services.parser.base import (
    ExtractedInvoice,
    ExtractionResult,
    ParserError,
    PermanentParserError,
    TransientParserError,
)
from backend.app.services.parser.invoice_parser import InvoiceParser
from backend.app.services.parser.ocr import extract_text_via_ocr
from backend.app.services.parser.pdf_text import extract_text_from_pdf

__all__ = [
    "InvoiceParser",
    "ExtractedInvoice",
    "ExtractionResult",
    "ParserError",
    "PermanentParserError",
    "TransientParserError",
    "extract_text_from_pdf",
    "extract_text_via_ocr",
]

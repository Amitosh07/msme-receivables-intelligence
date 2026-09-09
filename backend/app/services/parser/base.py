"""
Parser data structures and custom exceptions.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional


class ParserError(Exception):
    """Base exception for parser errors."""
    pass


class PermanentParserError(ParserError):
    """Raised when an invoice document is permanently unparseable or malformed."""
    pass


class TransientParserError(ParserError):
    """Raised when a temporary processing or extraction failure occurs (e.g. storage I/O)."""
    pass


@dataclass
class ExtractedInvoice:
    """Structured representation of extracted invoice data."""
    invoice_number: str
    invoice_date: date
    due_date: date
    amount: float
    currency: str
    customer_name: Optional[str] = None
    customer_ref: Optional[str] = None
    payment_terms: Optional[str] = None
    extraction_method: str = "text"  # "text" or "ocr"
    confidence: float = 1.0
    raw_text: str = ""


@dataclass
class ExtractionResult:
    """Outcome of invoice extraction attempt."""
    success: bool
    invoice: Optional[ExtractedInvoice] = None
    error: Optional[str] = None
    method: str = "text"

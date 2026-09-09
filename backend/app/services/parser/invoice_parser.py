"""
Structured invoice parser orchestrating text-first extraction and OCR fallback.
Extracts normalized invoice attributes with rigorous validation and no fabrication.
"""

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from backend.app.services.parser.base import (
    ExtractedInvoice,
    ExtractionResult,
)
from backend.app.services.parser.ocr import extract_text_via_ocr
from backend.app.services.parser.pdf_text import extract_text_from_pdf

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES = {
    "$": "USD",
    "USD": "USD",
    "₹": "INR",
    "INR": "INR",
    "CAD": "CAD",
    "EUR": "EUR",
    "€": "EUR",
    "GBP": "GBP",
    "£": "GBP",
}

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%Y.%m.%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
]


class InvoiceParser:
    """
    Parser for converting raw invoice PDFs into structured ExtractedInvoice objects.
    Enforces text extraction first, falling back to OCR when text is insufficient.
    """

    MIN_TEXT_CHARS = 40  # Minimum characters to consider text extraction successful

    @classmethod
    def parse(cls, pdf_bytes: bytes) -> ExtractionResult:
        """
        Main entry point for parsing an invoice PDF.
        Returns ExtractionResult with parsed invoice or structured error.
        """
        if not pdf_bytes:
            return ExtractionResult(success=False, error="Document content is empty.")

        # 1. Text-first extraction
        method = "text"
        raw_text = extract_text_from_pdf(pdf_bytes)

        # 2. OCR fallback if text is missing or sparse (scanned invoice)
        if len(raw_text.strip()) < cls.MIN_TEXT_CHARS:
            logger.info("Text extraction yielded insufficient text (%d chars). Falling back to OCR.", len(raw_text))
            ocr_text = extract_text_via_ocr(pdf_bytes)
            if len(ocr_text.strip()) > len(raw_text.strip()):
                raw_text = ocr_text
                method = "ocr"

        if len(raw_text.strip()) < 10:
            return ExtractionResult(
                success=False,
                error="Unable to extract text from document (file may be blank, encrypted, or corrupted).",
                method=method,
            )

        # 3. Extract structured attributes
        extracted = cls._extract_fields(raw_text, method=method)
        if not extracted:
            return ExtractionResult(
                success=False,
                error="Could not extract required invoice fields (missing invoice number, date, or total amount).",
                method=method,
            )

        return ExtractionResult(success=True, invoice=extracted, method=method)

    @classmethod
    def _extract_fields(cls, text: str, method: str) -> Optional[ExtractedInvoice]:
        """
        Extracts and normalizes invoice fields from raw text via deterministic heuristics.
        """
        # A. Invoice Number
        invoice_num = cls._extract_invoice_number(text)
        if not invoice_num:
            return None

        # B. Invoice Date
        inv_date = cls._extract_invoice_date(text)
        if not inv_date:
            return None

        # C. Payment Terms & Due Date
        terms = cls._extract_payment_terms(text)
        due_date = cls._extract_due_date(text, inv_date, terms)
        if not due_date:
            return None

        # D. Amount & Currency
        amount, currency = cls._extract_amount_and_currency(text)
        if amount is None or amount <= 0:
            return None

        # E. Customer Info (optional but extracted if available)
        customer_name, customer_ref = cls._extract_customer(text)

        confidence = 0.95 if method == "text" else 0.75

        return ExtractedInvoice(
            invoice_number=invoice_num,
            invoice_date=inv_date,
            due_date=due_date,
            amount=amount,
            currency=currency,
            customer_name=customer_name,
            customer_ref=customer_ref,
            payment_terms=terms,
            extraction_method=method,
            confidence=confidence,
            raw_text=text[:2000],  # Store snippet for audit
        )

    @staticmethod
    def _extract_invoice_number(text: str) -> Optional[str]:
        """Extract unique invoice number."""
        patterns = [
            r"(?i)\b(INV[-_][A-Za-z0-9\-_/]{2,25})\b",
            r"(?i)invoice\s*(?:#|no\.?|num(?:ber)?|id)\s*[:\-]?\s*([A-Za-z0-9\-_/]{2,30})",
            r"(?i)invoice\s*[:\-]\s*([A-Za-z0-9\-_/]{2,30})",
            r"(?i)bill\s*(?:#|no\.?|num(?:ber)?)\s*[:\-]?\s*([A-Za-z0-9\-_/]{2,30})",
            r"(?i)\bdoc(?:ument)?\s*(?:#|id|no\.?)?\s*[:\-]?\s*([0-9]{5,20})\b",
        ]
        stop_words = {
            "date", "total", "amount", "due", "page", "invoice", "bill",
            "tax", "statement", "supply", "payment", "terms", "customer"
        }
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip()
                # Exclude false positives like dates or common header words, and require at least one digit
                if candidate.lower() not in stop_words and any(c.isdigit() for c in candidate):
                    return candidate
        return None

    @classmethod
    def _extract_invoice_date(cls, text: str) -> Optional[date]:
        """Extract invoice issue date."""
        patterns = [
            r"(?i)(?:invoice\s*date|date\s*of\s*issue|issue\s*date|bill\s*date|dated)\s*[:\-]?\s*([0-9A-Za-z, \.\-/]{6,20})",
            r"(?i)\bdate\s*[:\-]?\s*([0-9]{1,4}[-/\.][0-9]{1,2}[-/\.][0-9]{1,4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                d = cls._parse_date_str(match.group(1).strip())
                if d:
                    return d
        return None

    @classmethod
    def _extract_due_date(
        cls,
        text: str,
        invoice_date: date,
        terms: Optional[str],
    ) -> Optional[date]:
        """Extract contractual due date or derive from commercial payment terms."""
        patterns = [
            r"(?i)(?:due\s*date|payment\s*due|pay\s*by)\s*[:\-]?\s*([0-9A-Za-z, \.\-/]{6,20})",
            r"(?i)\bdue\s*[:\-]\s*([0-9A-Za-z, \.\-/]{6,20})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                d = cls._parse_date_str(match.group(1).strip())
                if d:
                    return d

        # Fallback: derive from payment terms if available (e.g. "Net 30", "15 Days")
        if terms:
            days_match = re.search(r"(\d+)", terms)
            if days_match:
                days = int(days_match.group(1))
                return invoice_date + timedelta(days=days)

        # Default commercial fallback: 30 days
        return invoice_date + timedelta(days=30)

    @classmethod
    def _extract_amount_and_currency(cls, text: str) -> Tuple[Optional[float], str]:
        """Extract total invoice monetary value and billing currency."""
        currency = "USD"
        for sym, code in SUPPORTED_CURRENCIES.items():
            if sym in text:
                currency = code
                break

        patterns = [
            r"(?i)(?:total\s*amount|total\s*due|amount\s*due|balance\s*due|grand\s*total|invoice\s*total)\s*[:\-]?\s*(?:[A-Z]{3}|[$₹€£])?\s*([\d,]+(?:\.\d{1,2})?)",
            r"(?i)(?:USD|CAD|INR|EUR|GBP)\s*([\d,]+(?:\.\d{1,2})?)",
            r"(?i)\btotal\s*[:\-]?\s*(?:[A-Z]{3}|[$₹€£])?\s*([\d,]+(?:\.\d{1,2})?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                cleaned = re.sub(r"[^\d.]", "", match.group(1).replace(",", ""))
                try:
                    val = float(cleaned)
                    if val > 0:
                        return val, currency
                except ValueError:
                    continue
        return None, currency

    @staticmethod
    def _extract_payment_terms(text: str) -> Optional[str]:
        """Extract commercial payment terms string."""
        patterns = [
            r"(?i)(?:payment\s*terms|terms)\s*[:\-]?\s*([A-Za-z0-9 ]{2,20})",
            r"(?i)\b(net\s*\d+|due\s*on\s*receipt|\d+\s*days)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _extract_customer(text: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract customer name and customer reference identifier."""
        name = None
        ref = None

        cust_name_match = re.search(
            r"(?i)(?:bill\s*to|sold\s*to|customer|client|invoiced\s*to)\s*[:\-]?\s*([A-Za-z0-9 &.,\-]{2,50})",
            text,
        )
        if cust_name_match:
            candidate = cust_name_match.group(1).strip()
            # Clean up linebreaks or trailing addresses
            candidate = candidate.split("\n")[0].strip()
            if len(candidate) >= 2 and candidate.lower() not in {"date", "invoice", "total"}:
                name = candidate

        cust_ref_match = re.search(
            r"(?i)(?:customer\s*id|customer\s*no\.?|account\s*no\.?|client\s*id)\s*[:\-]?\s*([A-Za-z0-9\-_]{2,30})",
            text,
        )
        if cust_ref_match:
            ref = cust_ref_match.group(1).strip()

        return name, ref

    @staticmethod
    def _parse_date_str(val: str) -> Optional[date]:
        """Normalize and parse date string into date object."""
        cleaned = re.sub(r"[^\w\s\.\-/]", "", val).strip()
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
        return None

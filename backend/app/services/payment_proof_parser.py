"""
Payment Proof extraction engine for PDF, CSV, and XLSX payment evidence.
Normalizes heterogeneous inputs into a single structured candidate format.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import io
import logging
import re
from typing import Any, Dict, List, Optional
import unicodedata

from backend.app.services.parser.normalization import normalize_invoice_text
from backend.app.services.parser.ocr import extract_text_via_ocr, ocr_availability
from backend.app.services.parser.pdf_text import extract_layout_text_from_pdf, extract_text_from_pdf
from backend.app.services.payment_import_service import (
    _cell_text,
    _decode_csv,
    _find_column,
    _normalize_reference_for_invoice_match,
    _parse_amount,
    _parse_date,
)

logger = logging.getLogger(__name__)

# Common regex patterns for free-text receipts / vouchers
INVOICE_REF_PATTERNS = (
    re.compile(
        r"(?i)(?:against\s+invoice|tax\s+invoice|invoice|inv\.?|bill|doc(?:ument)?|ref(?:erence)?)"
        r"(?:\s*(?:no\.?|num(?:ber)?|#|id))?\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_.-]{1,63})"
    ),
    re.compile(
        r"(?i)\b((?:INV|SI|SO|BILL|DOC)[A-Z0-9]*[-_/][A-Z0-9/_.-]{1,60})\b"
    ),
)

AMOUNT_PATTERNS = (
    re.compile(
        r"(?i)(?:amount\s+paid|paid\s+amount|payment\s+amount|received\s+amount|total\s+paid|"
        r"net\s+paid|amount\s+received|amount|paid|total|net)\s*[:#-]?\s*"
        r"(?:(?:INR|USD|CAD|EUR|GBP|AUD|RS\.?|[₹$€£\ufffd]|[^\w\s\d])\s*)?"
        r"((?:\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?))\b"
    ),
    re.compile(
        r"(?:(?:INR|USD|CAD|EUR|GBP|AUD|RS\.?|[₹$€£\ufffd]|[^\w\s\d])\s*)"
        r"((?:\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?))\b"
    ),
)

DATE_PATTERNS = (
    re.compile(
        r"(?i)(?:payment\s+date|paid\s+on|paid\s+date|date\s+of\s+payment|transaction\s+date|"
        r"txn\s+date|value\s+date|received\s+date|clear\s+date|date)\s*[:#-]?\s*"
        r"(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    ),
    re.compile(
        r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b"
    ),
    re.compile(
        r"\b(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b"
    ),
    re.compile(
        r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
        re.I,
    ),
)

TRANSACTION_REF_PATTERNS = (
    re.compile(
        r"(?i)(?:utr(?:\s*no\.?)?|transaction\s+id|txn\s+id|transaction\s+ref|bank\s+ref|"
        r"cheque\s+no\.?|payment\s+ref(?:erence)?|ref\s+no\.?)\s*[:#-]?\s*([A-Z0-9]{6,40})"
    ),
)

CUSTOMER_PATTERNS = (
    re.compile(
        r"(?i)(?:paid\s+by|from|received\s+from|customer|client|payer|buyer|company)\s*[:#-]?\s*"
        r"([A-Za-z0-9\s.,&'-]{3,80})"
    ),
)

GSTIN_PATTERN = re.compile(
    r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b"
)


@dataclass(frozen=True)
class ExtractedProof:
    """Normalized output from any supported proof document format."""
    invoice_reference: Optional[str] = None
    payment_date: Optional[datetime] = None
    amount: Optional[Decimal] = None
    customer_name: Optional[str] = None
    customer_gstin: Optional[str] = None
    payment_reference: Optional[str] = None
    confidence: float = 0.0
    candidate_count: int = 1
    extraction_method: str = "unknown"
    raw_snippet: Optional[str] = None


class PaymentProofParser:
    """Unified parser for PDF, CSV, and XLSX payment proofs."""

    @classmethod
    def parse(
        cls,
        file_bytes: bytes,
        filename: str,
        content_type: str = "",
    ) -> ExtractedProof:
        """Parse bytes into an ExtractedProof based on file format."""
        lower_name = (filename or "").lower()
        if lower_name.endswith(".pdf") or "pdf" in content_type:
            return cls._parse_pdf(file_bytes)
        elif lower_name.endswith(".csv") or "csv" in content_type:
            return cls._parse_csv(file_bytes)
        elif lower_name.endswith(".xlsx") or "spreadsheet" in content_type or "excel" in content_type:
            return cls._parse_xlsx(file_bytes)
        else:
            raise ValueError(
                f"Unsupported payment proof file format '{filename}'. "
                f"Accepted formats are PDF, CSV, and XLSX."
            )

    @classmethod
    def _parse_pdf(cls, pdf_bytes: bytes) -> ExtractedProof:
        """Layered PDF extraction: Native text -> Layout text -> OCR fallback."""
        if not pdf_bytes or len(pdf_bytes) < 4:
            raise ValueError("The uploaded payment proof file is empty.")
        if b"%PDF" not in pdf_bytes[:1024]:
            raise ValueError("The uploaded file is not a valid PDF document.")

        # 1. Native text
        native_text = normalize_invoice_text(extract_text_from_pdf(pdf_bytes))
        if native_text:
            extracted = cls._extract_from_text(native_text, method="native_text")
            if extracted.amount is not None and extracted.payment_date is not None:
                return extracted

        # 2. Layout text
        layout_text = normalize_invoice_text(extract_layout_text_from_pdf(pdf_bytes))
        if layout_text and layout_text != native_text:
            extracted_layout = cls._extract_from_text(layout_text, method="layout_text")
            if extracted_layout.amount is not None and extracted_layout.payment_date is not None:
                return extracted_layout
            # If layout found something native didn't, prefer it
            if (extracted_layout.amount is not None or extracted_layout.payment_date is not None) and (
                native_text is None or extracted.amount is None
            ):
                extracted = extracted_layout

        # If native/layout got both, return
        if 'extracted' in locals() and extracted.amount is not None and extracted.payment_date is not None:
            return extracted

        # 3. OCR fallback
        available, reason = ocr_availability()
        if available:
            ocr_text = normalize_invoice_text(extract_text_via_ocr(pdf_bytes), ocr=True)
            if ocr_text:
                ocr_extracted = cls._extract_from_text(ocr_text, method="ocr")
                if ocr_extracted.amount is not None or ocr_extracted.payment_date is not None:
                    return ocr_extracted
        else:
            logger.info("OCR fallback unavailable for payment proof: %s", reason)

        # Return best candidate found or empty candidate
        if 'extracted' in locals():
            return extracted
        return ExtractedProof(extraction_method="pdf_insufficient_text")

    @classmethod
    def _extract_from_text(cls, text: str, method: str) -> ExtractedProof:
        """Extract proof candidate fields from unstructured text."""
        cleaned_text = unicodedata.normalize("NFKC", text or "").strip()
        if not cleaned_text:
            return ExtractedProof(extraction_method=method)

        # 1. Extract Invoice Reference
        invoice_ref: Optional[str] = None
        for pattern in INVOICE_REF_PATTERNS:
            match = pattern.search(cleaned_text)
            if match:
                val = match.group(1).strip()
                # Basic sanity check (not generic words)
                if val.upper() not in ("PAID", "DATE", "AMOUNT", "TOTAL", "INR", "USD"):
                    invoice_ref = val[:64]
                    break

        # 2. Extract Amount
        amount: Optional[Decimal] = None
        for pattern in AMOUNT_PATTERNS:
            for match in pattern.finditer(cleaned_text):
                parsed = _parse_amount(match.group(1))
                if parsed is not None and parsed > 0:
                    amount = parsed
                    break
            if amount is not None:
                break

        # 3. Extract Date
        payment_date: Optional[datetime] = None
        for pattern in DATE_PATTERNS:
            for match in pattern.finditer(cleaned_text):
                parsed_dt = _parse_date(match.group(1))
                if parsed_dt is not None:
                    payment_date = parsed_dt
                    break
            if payment_date is not None:
                break

        # 4. Extract Payment Reference / UTR
        payment_ref: Optional[str] = None
        for pattern in TRANSACTION_REF_PATTERNS:
            match = pattern.search(cleaned_text)
            if match:
                payment_ref = match.group(1).strip()[:128]
                break

        # 5. Extract Customer / GSTIN
        customer_gstin: Optional[str] = None
        gstin_match = GSTIN_PATTERN.search(cleaned_text)
        if gstin_match:
            customer_gstin = gstin_match.group(1).strip().upper()

        customer_name: Optional[str] = None
        for pattern in CUSTOMER_PATTERNS:
            match = pattern.search(cleaned_text)
            if match:
                c_val = match.group(1).strip()
                # Clean out punctuation or line breaks
                first_line = c_val.split("\n")[0].strip()
                if len(first_line) >= 3:
                    customer_name = first_line[:255]
                    break

        confidence = 0.5
        if invoice_ref:
            confidence += 0.2
        if amount:
            confidence += 0.2
        if payment_date:
            confidence += 0.1

        return ExtractedProof(
            invoice_reference=invoice_ref,
            payment_date=payment_date,
            amount=amount,
            customer_name=customer_name,
            customer_gstin=customer_gstin,
            payment_reference=payment_ref,
            confidence=min(confidence, 1.0),
            extraction_method=method,
            raw_snippet=cleaned_text[:500],
        )

    @classmethod
    def _parse_csv(cls, file_bytes: bytes) -> ExtractedProof:
        """Parse structured CSV payment receipt."""
        content_str = _decode_csv(file_bytes)
        reader = csv.reader(io.StringIO(content_str))
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        if not rows:
            raise ValueError("The uploaded CSV file contains no data rows.")

        headers = [h.strip() for h in rows[0]]
        # Check if first row is header
        inv_col = _find_column(headers, "invoice")
        date_col = _find_column(headers, "date")
        amount_col = _find_column(headers, "amount")

        if inv_col or date_col or amount_col:
            # Table format with headers
            header_map = {name: idx for idx, name in enumerate(headers)}
            data_rows = rows[1:]
            if not data_rows:
                raise ValueError("CSV contains headers but no payment data rows.")
            # A proof with more than one populated candidate must never be
            # silently assigned to the upload target.  Keep the first row for
            # diagnostics, but surface the candidate count to verification.
            populated_rows = [
                row for row in data_rows
                if any(str(cell).strip() for cell in row)
            ]
            target_row = populated_rows[0]
            row_dict = {
                header: target_row[idx] if idx < len(target_row) else ""
                for header, idx in header_map.items()
            }
            invoice_val = row_dict.get(inv_col or "", "").strip() if inv_col else None
            date_val = _parse_date(row_dict.get(date_col or "", "")) if date_col else None
            amount_val = _parse_amount(row_dict.get(amount_col or "", "")) if amount_col else None

            # Optional fields
            customer_col = _find_column(headers, "customer_name")
            customer_val = row_dict.get(customer_col or "", "").strip() if customer_col else None
            ref_col = _find_column(headers, "payment_reference")
            ref_val = row_dict.get(ref_col or "", "").strip() if ref_col else None
            gstin_col = _find_column(headers, "gstin")
            gstin_val = row_dict.get(gstin_col or "", "").strip() if gstin_col else None

            return ExtractedProof(
                invoice_reference=invoice_val or None,
                payment_date=date_val,
                amount=amount_val,
                customer_name=customer_val or None,
                customer_gstin=gstin_val or None,
                payment_reference=ref_val or None,
                confidence=0.9,
                candidate_count=len(populated_rows),
                extraction_method="csv",
                raw_snippet=",".join(target_row[:10]),
            )
        else:
            # Free-form CSV / key-value format (e.g. "Amount, 50000", "Date, 2026-02-10")
            combined_text = "\n".join(" ".join(row) for row in rows)
            return cls._extract_from_text(combined_text, method="csv_text")

    @classmethod
    def _parse_xlsx(cls, file_bytes: bytes) -> ExtractedProof:
        """Parse structured XLSX spreadsheet receipt."""
        if not file_bytes or len(file_bytes) < 4:
            raise ValueError("The uploaded XLSX file is empty.")
        if file_bytes[:4] != b"PK\x03\x04":
            raise ValueError("The uploaded file is not a valid XLSX document.")

        try:
            import openpyxl
        except ImportError:
            raise RuntimeError("openpyxl dependency is required for XLSX payment proof parsing.")

        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        sheet = wb.active
        if not sheet:
            raise ValueError("The uploaded XLSX workbook has no active worksheet.")

        rows: List[List[Any]] = []
        for row in sheet.iter_rows(values_only=True):
            if row and any(cell is not None and str(cell).strip() for cell in row):
                rows.append([_cell_text(c) for c in row])

        wb.close()
        if not rows:
            raise ValueError("The uploaded XLSX worksheet is empty.")

        # Convert rows to standard CSV-like representation and reuse
        headers = rows[0]
        inv_col = _find_column(headers, "invoice")
        date_col = _find_column(headers, "date")
        amount_col = _find_column(headers, "amount")

        if inv_col or date_col or amount_col:
            header_map = {name: idx for idx, name in enumerate(headers)}
            data_rows = rows[1:]
            if not data_rows:
                raise ValueError("XLSX contains headers but no data rows.")
            populated_rows = [
                row for row in data_rows
                if any(str(cell).strip() for cell in row)
            ]
            target_row = populated_rows[0]
            row_dict = {
                header: target_row[idx] if idx < len(target_row) else ""
                for header, idx in header_map.items()
            }
            invoice_val = row_dict.get(inv_col or "", "").strip() if inv_col else None
            date_val = _parse_date(row_dict.get(date_col or "", "")) if date_col else None
            amount_val = _parse_amount(row_dict.get(amount_col or "", "")) if amount_col else None

            customer_col = _find_column(headers, "customer_name")
            customer_val = row_dict.get(customer_col or "", "").strip() if customer_col else None
            ref_col = _find_column(headers, "payment_reference")
            ref_val = row_dict.get(ref_col or "", "").strip() if ref_col else None
            gstin_col = _find_column(headers, "gstin")
            gstin_val = row_dict.get(gstin_col or "", "").strip() if gstin_col else None

            return ExtractedProof(
                invoice_reference=invoice_val or None,
                payment_date=date_val,
                amount=amount_val,
                customer_name=customer_val or None,
                customer_gstin=gstin_val or None,
                payment_reference=ref_val or None,
                confidence=0.9,
                candidate_count=len(populated_rows),
                extraction_method="xlsx",
                raw_snippet=",".join(target_row[:10]),
            )
        else:
            combined_text = "\n".join(" ".join(row) for row in rows)
            return cls._extract_from_text(combined_text, method="xlsx_text")

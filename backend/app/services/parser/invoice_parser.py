"""Layered, deterministic invoice parser for varied real-world PDF layouts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import re
from typing import Iterable, Optional

from backend.app.services.parser.base import ExtractedInvoice, ExtractionResult
from backend.app.services.parser.normalization import normalize_invoice_text
from backend.app.services.parser.ocr import extract_text_via_ocr, ocr_availability
from backend.app.services.parser.pdf_text import extract_layout_text_from_pdf, extract_text_from_pdf

logger = logging.getLogger(__name__)

DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d-%m-%Y", "%d/%m/%Y",
    "%d.%m.%Y", "%m-%d-%Y", "%m/%d/%Y", "%b %d %Y", "%B %d %Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%d %b, %Y", "%d %B, %Y",
)
DATE_VALUE = (
    r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|(?:\d{1,2}(?:st|nd|rd|th)?\s+)?[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}[,]?\s+\d{4})"
)
INVOICE_NUMBER_PATTERNS = (
    r"(?im)^\s*(?:tax\s+invoice(?:\s+(?:number|no\.?))?|order\s+invoice\s+(?:number|no\.?)|invoice|inv\.?|bill|document|reference|ref)\s*(?:number|no\.?|num(?:ber)?|#|id)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_.-]{1,63})\s*$",
    r"(?i)\b((?:(?:INV|SI|SO|BILL|DOC)[A-Z]*[-_/])(?:[A-Z0-9]+[-_/])?[A-Z0-9][A-Z0-9/_.-]{1,62})\b",
)
CURRENCY_CODES = {
    "INR": ("₹", "INR", "RS.", "RS ", "RUPEES"),
    "USD": ("US$", "USD", "$"), "CAD": ("C$", "CAD"),
    "EUR": ("EUR", "€"), "GBP": ("GBP", "£"), "AUD": ("A$", "AUD"),
}
TOTAL_LABELS = (
    ("grand total", 1.00), ("invoice total", 0.98), ("total amount", 0.98),
    ("amount due", 0.98), ("balance due", 0.97), ("total payable", 0.96),
    ("net payable", 0.96), ("payment due", 0.93), ("total due", 0.93),
    ("total", 0.82),
)


@dataclass(frozen=True)
class _Candidate:
    value: object
    score: float


class InvoiceParser:
    """Extract required invoice-level fields without depending on one template."""

    MIN_TEXT_CHARS = 30

    @classmethod
    def parse(cls, pdf_bytes: bytes) -> ExtractionResult:
        if not pdf_bytes:
            return ExtractionResult(False, error="The uploaded PDF is empty.", error_code="EMPTY_PDF")
        if b"%PDF" not in pdf_bytes[:1024]:
            return ExtractionResult(False, error="The uploaded file is not a valid PDF.", error_code="INVALID_PDF")

        warnings: list[str] = []
        attempts: list[tuple[str, str]] = []
        native = normalize_invoice_text(extract_text_from_pdf(pdf_bytes))
        if native:
            attempts.append(("text", native))
        layout = normalize_invoice_text(extract_layout_text_from_pdf(pdf_bytes))
        if layout and layout != native:
            attempts.append(("layout_text", layout))

        successful: list[ExtractedInvoice] = []
        missing_by_method: list[tuple[str, list[str]]] = []
        for method, text in attempts:
            invoice, missing = cls._extract_fields(text, method)
            if invoice:
                successful.append(invoice)
            else:
                missing_by_method.append((method, missing))
        if successful:
            best = max(successful, key=lambda item: item.confidence)
            return ExtractionResult(True, invoice=best, method=best.extraction_method)

        # OCR is last because it is slower and less precise than native/layout text.
        available, availability_reason = ocr_availability()
        if available:
            ocr_text = normalize_invoice_text(extract_text_via_ocr(pdf_bytes), ocr=True)
            if ocr_text:
                invoice, missing = cls._extract_fields(ocr_text, "ocr")
                if invoice:
                    return ExtractionResult(True, invoice=invoice, method="ocr")
                missing_by_method.append(("ocr", missing))
            else:
                warnings.append("OCR produced insufficient text.")
        else:
            warnings.append(availability_reason)

        usable_text = max((len(text) for _, text in attempts), default=0)
        if usable_text < cls.MIN_TEXT_CHARS:
            if not available:
                return ExtractionResult(
                    False,
                        error="Unable to extract readable invoice text. This may be a scanned PDF, and OCR is unavailable on this machine.",
                    error_code="OCR_UNAVAILABLE", method="ocr", warnings=warnings,
                )
            return ExtractionResult(
                False, error="Unable to extract readable invoice text, including after OCR.",
                error_code="UNREADABLE_PDF", method="ocr", warnings=warnings,
            )

        missing: list[str] = []
        for _, fields in missing_by_method:
            for field in fields:
                if field not in missing:
                    missing.append(field)
        detail = ", ".join(missing) if missing else "required invoice fields"
        return ExtractionResult(
            False, error=f"Could not reliably extract required fields: {detail}.",
            error_code="REQUIRED_FIELDS_MISSING",
            method=missing_by_method[-1][0] if missing_by_method else "text",
            warnings=warnings,
        )

    @classmethod
    def _extract_fields(cls, text: str, method: str) -> tuple[Optional[ExtractedInvoice], list[str]]:
        number = cls._extract_invoice_number(text)
        invoice_date = cls._extract_labeled_date(
            text, ("invoice date", "date of invoice", "issue date", "date of issue", "bill date", "document date", "date"),
        )
        terms, terms_confidence = cls._extract_payment_terms(text)
        due_date = cls._extract_labeled_date(
            text, ("due date", "payment due", "due on", "pay by", "payment date"),
            not_before=invoice_date.value if invoice_date else None,
        )
        if not due_date and invoice_date and terms:
            term_days = cls._term_days(terms)
            if term_days is not None:
                due_date = _Candidate(invoice_date.value + timedelta(days=term_days), 0.82)
        amount, currency = cls._extract_amount_and_currency(text)
        customer_name, customer_ref, customer_confidence = cls._extract_customer(text)

        required = {
            "invoice number": number, "invoice date": invoice_date,
            "due date or usable payment terms": due_date,
            "invoice total": amount, "currency": currency,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            return None, missing

        field_confidence = {
            "invoice_number": number.score, "invoice_date": invoice_date.score,
            "due_date": due_date.score, "amount": amount.score, "currency": currency.score,
            "payment_terms": terms_confidence, "customer_name": customer_confidence,
        }
        confidence = sum(field_confidence[name] for name in ("invoice_number", "invoice_date", "due_date", "amount", "currency")) / 5
        if method == "ocr":
            confidence *= 0.85
        return ExtractedInvoice(
            invoice_number=str(number.value), invoice_date=invoice_date.value, due_date=due_date.value,
            amount=float(amount.value), currency=str(currency.value), customer_name=customer_name,
            customer_ref=customer_ref, payment_terms=terms, extraction_method=method,
            confidence=round(confidence, 3), field_confidence=field_confidence, raw_text=text[:4000],
        ), []

    @staticmethod
    def _extract_invoice_number(text: str) -> Optional[_Candidate]:
        stop_words = {"date", "total", "amount", "due", "page", "invoice", "bill", "tax", "gstin", "payment", "terms", "customer", "number", "no"}
        candidates: list[_Candidate] = []
        for index, pattern in enumerate(INVOICE_NUMBER_PATTERNS):
            for match in re.finditer(pattern, text):
                value = match.group(1).strip(" .:#-")
                if value.casefold() in stop_words or not any(char.isdigit() for char in value):
                    continue
                compact = re.sub(r"\W", "", value)
                # Reject common non-invoice identifiers before accepting a fallback.
                if (InvoiceParser._parse_date_str(value) or len(compact) < 3
                        or re.fullmatch(r"\d{10,}", compact)
                        or re.fullmatch(r"\d{15}", compact)
                        or re.match(r"(?i)^(?:GSTIN|PAN|IFSC|PO)[-:/]?", value)):
                    continue
                candidates.append(_Candidate(value, 0.98 - index * 0.08))
        # A labelled field can be split by PDF layout extraction: "Invoice No"
        # on one line and the value on the following line.
        lines = [line.strip() for line in text.splitlines()]
        label = re.compile(r"(?i)^(?:tax\s+invoice|invoice|inv\.?|bill|document|reference|ref)\s*(?:number|no\.?|#|id)?\s*[:#-]?$")
        for index, line in enumerate(lines[:-1]):
            if label.match(line):
                value = lines[index + 1].strip(" .:#-")
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.-]{2,63}", value) and any(c.isdigit() for c in value):
                    candidates.append(_Candidate(value, 0.9))
        return max(candidates, key=lambda item: item.score, default=None)

    @classmethod
    def _extract_labeled_date(cls, text: str, labels: Iterable[str], not_before: Optional[date] = None) -> Optional[_Candidate]:
        candidates: list[_Candidate] = []
        for label_index, label in enumerate(labels):
            pattern = rf"(?i)\b{re.escape(label)}\b\s*[:#-]?\s*({DATE_VALUE})"
            for match in re.finditer(pattern, text):
                parsed = cls._parse_date_str(match.group(1))
                if parsed:
                    score = 0.98 - min(label_index, 5) * 0.015
                    if not_before and parsed < not_before:
                        score -= 0.55
                    candidates.append(_Candidate(parsed, score))
        return max((item for item in candidates if item.score >= 0.6), key=lambda item: item.score, default=None)

    @staticmethod
    def _parse_date_str(value: str) -> Optional[date]:
        cleaned = re.sub(r"(?i)(\d)(st|nd|rd|th)\b", r"\1", value)
        cleaned = re.sub(r"\s+", " ", cleaned.strip(" :#-"))
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(cleaned, fmt).date()
                if 1990 <= parsed.year <= 2100:
                    return parsed
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_payment_terms(text: str) -> tuple[Optional[str], float]:
        patterns = (
            r"(?i)\b(?:payment\s+terms|terms\s+of\s+payment|credit\s+terms|terms)\b\s*[:#-]?\s*(net\s*\d{1,3}|due\s+on\s+receipt|cash\s+on\s+delivery|COD|\d{1,3}\s*(?:calendar\s+)?days?)",
            r"(?i)\b(net\s*\d{1,3}|due\s+on\s+receipt|cash\s+on\s+delivery|COD)\b",
        )
        for index, pattern in enumerate(patterns):
            match = re.search(pattern, text)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip(), 0.96 - index * 0.1
        return None, 0.0

    @staticmethod
    def _term_days(terms: str) -> Optional[int]:
        if re.search(r"(?i)due\s+on\s+receipt|cash\s+on\s+delivery|\bCOD\b", terms):
            return 0
        match = re.search(r"\b(\d{1,3})\b", terms)
        if not match:
            return None
        days = int(match.group(1))
        return days if days <= 365 else None

    @classmethod
    def _extract_amount_and_currency(cls, text: str) -> tuple[Optional[_Candidate], Optional[_Candidate]]:
        amount_candidates: list[tuple[_Candidate, str]] = []
        # Prefer the unrestricted branch first; otherwise an ungrouped amount
        # such as 125000 is prematurely captured as 125.
        number = r"(?:\(?\s*)?\d+(?:,\d{2,3})*(?:\.\d{1,2})?"
        marker = r"(?:₹|\$|€|£|US\$|C\$|A\$|INR|USD|CAD|EUR|GBP|AUD|Rs\.?|Indian\s+Rupees?)?"
        for label, label_score in TOTAL_LABELS:
            pattern = rf"(?i)\b{re.escape(label)}\b\s*[:#-]?\s*({marker})[^\d]{{0,16}}({number})\s*([A-Z]{{3}}|₹|\$|€|£|Rs\.?)?"
            for match in re.finditer(pattern, text):
                parsed = cls._parse_money(match.group(2))
                if parsed and parsed > 0:
                    context = " ".join(part or "" for part in (match.group(1), match.group(3)))
                    score = min(1.0, label_score + min(0.03, len(str(int(parsed))) * 0.003))
                    amount_candidates.append((_Candidate(parsed, score), context))
        if not amount_candidates:
            return None, cls._currency_from_text(text)
        amount_candidate, context = max(amount_candidates, key=lambda item: item[0].score)
        currency = cls._currency_from_text(context, 0.99) or cls._currency_from_text(text)
        return amount_candidate, currency

    @staticmethod
    def _parse_money(value: str) -> Optional[float]:
        cleaned = value.strip().replace(" ", "")
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()").replace(",", "")
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
            return None
        amount = float(cleaned)
        return -amount if negative else amount

    @staticmethod
    def _currency_from_text(text: str, score: float = 0.9) -> Optional[_Candidate]:
        explicit = re.search(r"(?i)\bcurrency\b\s*[:#-]?\s*(INR|USD|CAD|EUR|GBP|AUD)\b", text)
        if explicit:
            return _Candidate(explicit.group(1).upper(), 0.99)
        upper = text.upper()
        matches: list[_Candidate] = []
        for code, markers in CURRENCY_CODES.items():
            if any((marker == "$" and re.search(r"(?<![A-Z])\$", text)) or (marker != "$" and (marker in text or marker.upper() in upper)) for marker in markers):
                matches.append(_Candidate(code, score - (0.03 if "$" in markers else 0)))
        if re.search(r"(?i)(?:\brs\.?\s*|\bindian\s+rupees?\b|\brupees?\b)\d", text):
            matches.append(_Candidate("INR", score))
        # The affected ReportLab PDFs map their embedded rupee glyph to a
        # literal capital I (U+0049) during native extraction; OCR drops it.
        # Treat this as INR only in this exact total-label + Indian grouping
        # context. It is never a global I-to-rupee substitution.
        if re.search(r"(?i)(?:grand\s+total|invoice\s+total|amount\s+due|total)\D{0,16}[I�·]\s*\d{1,3}(?:,\d{2})*,\d{3}", text):
            matches.append(_Candidate("INR", score - 0.05))
        return max(matches, key=lambda item: item.score, default=None)

    @staticmethod
    def _extract_customer(text: str) -> tuple[Optional[str], Optional[str], float]:
        lines = [line.strip(" :#-") for line in text.splitlines()]
        labels = re.compile(r"(?i)^(bill(?:ed)?\s+to|sold\s+to|invoice(?:d)?\s+to|customer|buyer|client)\b")
        structural = re.compile(r"(?i)^(ship\s+to|bill\s+to|invoice|tax invoice|date|due|payment|terms|description|qty|quantity|amount|total|gstin)\b")
        name: Optional[str] = None
        confidence = 0.0
        for index, line in enumerate(lines):
            if not labels.match(line):
                continue
            inline = labels.sub("", line, count=1).strip(" :#-")
            for candidate in [inline] + lines[index + 1:index + 7]:
                if not candidate or structural.match(candidate):
                    continue
                if re.search(r"[A-Za-z]", candidate) and not re.fullmatch(r"[A-Z]{2,5}-?\d+", candidate):
                    name = candidate[:255]
                    confidence = 0.92 if candidate == inline else 0.82
                    break
            if name:
                break
        ref_match = re.search(r"(?im)^\s*(?:customer|client|account)\s*(?:id|number|no\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9_.-]{1,63})\s*$", text)
        return name, ref_match.group(1).strip() if ref_match else None, confidence

"""Layered, deterministic invoice parser for varied real-world PDF layouts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import re
from typing import Iterable, Optional

from backend.app.services.parser.base import ExtractedInvoice, ExtractionResult
from backend.app.services.parser.normalization import normalize_invoice_text
from backend.app.services.customer_identity import normalize_gstin
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
    ("grand total", 1.00), ("total invoice value", 0.99), ("total amount", 0.98),
    ("invoice total", 0.98), ("total payable", 0.98), ("net payable", 0.97),
    ("amount due", 0.97), ("balance due", 0.96), ("total bill amount", 0.96),
    ("total due", 0.95), ("gross total", 0.95), ("total value", 0.95),
    ("net amount payable", 0.95), ("invoice value", 0.94), ("payment due", 0.93),
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
    def parse(cls, pdf_bytes: bytes, *, historical: bool = False) -> ExtractionResult:
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
            invoice, missing = cls._extract_fields(text, method, historical=historical)
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
                invoice, missing = cls._extract_fields(ocr_text, "ocr", historical=historical)
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
    def _extract_fields(
        cls, text: str, method: str, *, historical: bool = False
    ) -> tuple[Optional[ExtractedInvoice], list[str]]:
        number = cls._extract_invoice_number(text)
        invoice_date = cls._extract_labeled_date(
            text, ("invoice date", "date of invoice", "issue date", "date of issue", "bill date", "document date", "inv date", "date"),
        )
        terms, terms_confidence = cls._extract_payment_terms(text)
        due_date = cls._extract_labeled_date(
            text, ("due date", "payment due date", "payment due", "due on", "pay by", "due by", "payment date"),
            not_before=invoice_date.value if invoice_date else None,
        )
        if not due_date and invoice_date and terms:
            term_days = cls._term_days(terms)
            if term_days is not None:
                due_date = _Candidate(invoice_date.value + timedelta(days=term_days), 0.82)
        amount, currency = cls._extract_amount_and_currency(text)
        customer_name, customer_ref, customer_gstin, customer_confidence = cls._extract_customer(text)

        # Required extraction contract:
        # 1. invoice_number
        # 2. customer_name
        # 3. invoice_date
        # 4. total_amount
        # 5. due_date (explicit OR derived from payment terms)
        customer_candidate = (
            _Candidate(customer_name, customer_confidence)
            if customer_name and customer_confidence >= 0.6
            else None
        )
        required = ({"invoice total": amount} if historical else {
            "invoice number": number,
            "customer name": customer_candidate,
            "invoice date": invoice_date,
            "due date or usable payment terms": due_date,
            "invoice total": amount,
        })
        missing = [name for name, value in required.items() if value is None]
        if missing:
            return None, missing

        # Extract optional fields
        seller_name, seller_gstin = cls._extract_seller(text, customer_name, customer_gstin)
        subtotal, taxable_amount, cgst, sgst, igst = cls._extract_tax_breakdown(text)
        po_number = cls._extract_po_number(text)

        field_confidence = {
            "invoice_number": number.score if number else 0.0,
            "customer_name": customer_confidence,
            "invoice_date": invoice_date.score if invoice_date else 0.0,
            "due_date": due_date.score if due_date else 0.0,
            "amount": amount.score,
            "payment_terms": terms_confidence,
        }
        if currency:
            field_confidence["currency"] = currency.score

        score_components = [amount.score]
        score_components.extend(
            candidate.score for candidate in (number, invoice_date, due_date) if candidate
        )
        if customer_candidate:
            score_components.append(customer_confidence)
        if currency:
            score_components.append(currency.score)
        confidence = sum(score_components) / len(score_components)
        if method == "ocr":
            confidence *= 0.85

        return ExtractedInvoice(
            invoice_number=str(number.value) if number else None,
            invoice_date=invoice_date.value if invoice_date else None,
            due_date=due_date.value if due_date else None,
            amount=float(amount.value),
            currency=str(currency.value) if currency else None,
            customer_name=customer_name,
            customer_ref=customer_ref,
            customer_gstin=customer_gstin,
            seller_name=seller_name,
            seller_gstin=seller_gstin,
            subtotal=subtotal,
            taxable_amount=taxable_amount,
            cgst=cgst,
            sgst=sgst,
            igst=igst,
            purchase_order_number=po_number,
            payment_terms=terms,
            extraction_method=method,
            confidence=round(confidence, 3),
            field_confidence=field_confidence,
            raw_text=text[:4000],
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
            if label.lower() == "date":
                pattern = rf"(?i)(?<!\bdue\s)(?<!\bpayment\s)(?<!\bpay\s)(?<!\bexpiry\s)\bdate\b\s*[:#-]?\s*({DATE_VALUE})"
            else:
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
            r"(?i)\b(?:payment\s+terms|terms\s+of\s+payment|credit\s+terms|credit\s+period|terms)\b\s*[:#-]?\s*(net\s*\d{1,3}|due\s+on\s+receipt|cash\s+on\s+delivery|COD|\d{1,3}\s*(?:calendar\s+)?days?|within\s+\d{1,3}\s*days?)",
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
            pattern = rf"(?i)\b{re.escape(label)}\b\s*[:#-]?\s*({marker})[^\d]{{0,24}}({number})\s*([A-Z]{{3}}|₹|\$|€|£|Rs\.?)?"
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
        if re.search(r"(?i)(?:grand\s+total|invoice\s+total|amount\s+due|total)\D{0,16}[I·]\s*\d{1,3}(?:,\d{2})*,\d{3}", text):
            matches.append(_Candidate("INR", score - 0.05))
        return max(matches, key=lambda item: item.score, default=None)

    @staticmethod
    def _extract_customer(text: str) -> tuple[Optional[str], Optional[str], Optional[str], float]:
        lines = [line.strip(" :#-") for line in text.splitlines()]
        labels = re.compile(
            r"(?i)^(?:details\s+of\s+(?:receiver|buyer|recipient)(?:\s*[|/]\s*(?:billed\s+to|bill\s+to))?"
            r"|bill(?:ed)?\s+to(?:\s*\([^)]*\))?"
            r"|buyer\s*\([^)]*\)(?:\s*(?:name|details))?"
            r"|buyer(?:\s*(?:name|details))?"
            r"|consignee\s*\((?:billed\s+to|bill\s+to)\)"
            r"|customer(?:\s*(?:name|details))?|client(?:\s*(?:name|details))?"
            r"|party\s+name|name\s+of\s+party"
            r"|sold\s+to|invoice(?:d)?\s+to|recipient(?:\s*(?:name|details))?"
            r"|m/s\.?|messrs\.?)\s*(?:\([^)]*\))?\s*[:#-]*"
        )
        structural = re.compile(
            r"(?i)^(?:ship\s+to|shipped\s+to|consignee(?:\s*\([^)]*\))?"
            r"|tax\s+invoice|invoice(?:\s+no\.?)?|bill\s+of\s+supply"
            r"|date|due(?:\s+date)?|payment(?:\s+terms)?|terms"
            r"|description|qty|quantity|amount|total|subtotal|sub\s+total|rate"
            r"|gstin(?:\s*/\s*uin)?|pan(?:\s+no\.?)?|state(?:\s+code)?|cin(?:\s+no\.?)?"
            r"|phone|email|contact|original\s+for\s+recipient|duplicate|triplicate)\b"
        )
        name: Optional[str] = None
        gstin: Optional[str] = None
        confidence = 0.0

        for index, line in enumerate(lines):
            if not labels.match(line):
                continue
            inline = labels.sub("", line, count=1).strip(" :#-")
            candidates = [inline] + lines[index + 1:index + 8]
            for candidate in candidates:
                candidate_clean = candidate.strip(" :#-")
                if not candidate_clean or structural.match(candidate_clean):
                    continue
                # Skip standalone or parenthesized label lines like (Bill to), (Ship to)
                if re.match(r"(?i)^\(?\s*(?:bill\s+to|billed\s+to|ship\s+to|shipped\s+to|buyer|consignee)\s*\)?$", candidate_clean):
                    continue
                clean_name = re.sub(r"(?i)^m/s\.?\s+", "", candidate_clean).strip()
                clean_name = re.sub(r"(?i)^\(?\s*(?:bill\s+to|billed\s+to|ship\s+to|buyer)\s*\)?\s*", "", clean_name).strip(" :#-")
                if (
                    re.search(r"[A-Za-z]", clean_name)
                    and not re.fullmatch(r"[A-Z]{2,5}-?\d+", clean_name)
                    and len(clean_name) >= 2
                    and not re.match(r"(?i)^(?:GSTIN|PAN|CIN|STATE|INV|PO|TEL|MOB|PH)[-:/]?", clean_name)
                ):
                    name = clean_name[:255]
                    confidence = 0.94 if candidate == inline and inline else 0.86
                    break

            if name:
                gstin_label = re.compile(
                    r"(?i)\b(?:GSTIN(?:\s*/\s*UIN)?|GST\s+No\.?|GST\s+Identification\s+Number)"
                    r"(?![A-Za-z])\s*[:#-]?\s*(.*)$"
                )
                customer_lines = lines[index:index + 9]
                for offset, candidate_line in enumerate(customer_lines):
                    match = gstin_label.search(candidate_line)
                    if not match:
                        continue
                    val = match.group(1).strip()
                    if not val and offset + 1 < len(customer_lines):
                        val = customer_lines[offset + 1]
                    compact = re.sub(r"\s+", "", val).upper()
                    for possible in re.findall(r"[0-9A-Z]{15}", compact):
                        if normalize_gstin(possible):
                            gstin = possible
                            break
                    if gstin:
                        break
                break

        ref_match = re.search(
            r"(?im)^\s*(?:customer|client|account)\s*(?:id|number|no\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9_.-]{1,63})\s*$",
            text,
        )
        return name, ref_match.group(1).strip() if ref_match else None, gstin, confidence

    @staticmethod
    def _extract_seller(
        text: str,
        customer_name: Optional[str] = None,
        customer_gstin: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        lines = [line.strip(" :#-") for line in text.splitlines() if line.strip(" :#-")]
        seller_labels = re.compile(
            r"(?i)^(?:seller(?:\s*(?:name|details))?|supplier(?:\s*(?:name|details))?"
            r"|consignor(?:\s*(?:name|details))?|from|sold\s+by|biller)\b"
        )
        seller_name = None
        seller_gstin = None

        # Look for explicit seller label
        for index, line in enumerate(lines):
            if seller_labels.match(line):
                inline = seller_labels.sub("", line, count=1).strip(" :#-")
                for candidate in [inline] + lines[index + 1:index + 5]:
                    if not candidate:
                        continue
                    clean = re.sub(r"(?i)^m/s\.?\s+", "", candidate).strip()
                    if re.search(r"[A-Za-z]", clean) and len(clean) >= 2:
                        if not customer_name or clean.casefold() != customer_name.casefold():
                            seller_name = clean[:255]
                            break
                if seller_name:
                    break

        # Fallback to inspecting top header lines before buyer block
        if not seller_name and lines:
            header_skip = re.compile(
                r"(?i)^(?:tax\s+invoice|invoice|bill\s+of\s+supply|original\s+for\s+recipient"
                r"|duplicate|triplicate|quotation|estimate|proforma)\b"
            )
            for line in lines[:5]:
                if header_skip.match(line):
                    continue
                clean = re.sub(r"(?i)^m/s\.?\s+", "", line).strip()
                if (
                    re.search(r"[A-Za-z]", clean)
                    and len(clean) >= 3
                    and not re.search(r"(?i)^(?:bill\s+to|buyer|ship\s+to|date|invoice\s+no|gstin)", clean)
                ):
                    if not customer_name or clean.casefold() != customer_name.casefold():
                        seller_name = clean[:255]
                        break

        # Locate seller GSTIN distinct from customer GSTIN
        for match in re.finditer(r"(?i)\b(?:GSTIN(?:\s*/\s*UIN)?|GST\s+No\.?)\s*[:#-]?\s*([0-9A-Z]{15})\b", text):
            candidate_gstin = match.group(1).upper()
            if normalize_gstin(candidate_gstin):
                if not customer_gstin or candidate_gstin != customer_gstin:
                    seller_gstin = candidate_gstin
                    break

        return seller_name, seller_gstin

    @classmethod
    def _extract_tax_breakdown(cls, text: str) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
        subtotal = None
        taxable_amount = None
        cgst = None
        sgst = None
        igst = None

        taxable_m = re.search(r"(?i)\b(?:taxable\s+value|taxable\s+amount)\b\s*[:#-]?\s*(?:INR|Rs\.?|₹|\$)?\s*([0-9]+(?:,[0-9]{2,3})*(?:\.[0-9]{1,2})?)", text)
        if taxable_m:
            taxable_amount = cls._parse_money(taxable_m.group(1))

        subtotal_m = re.search(r"(?i)\b(?:sub\s*total)\b\s*[:#-]?\s*(?:INR|Rs\.?|₹|\$)?\s*([0-9]+(?:,[0-9]{2,3})*(?:\.[0-9]{1,2})?)", text)
        if subtotal_m:
            subtotal = cls._parse_money(subtotal_m.group(1))

        cgst_m = re.search(r"(?i)\b(?:cgst|central\s+gst)\b(?:\s*@\s*\d+(?:\.\d+)?%?)?\s*[:#-]?\s*(?:INR|Rs\.?|₹|\$)?\s*([0-9]+(?:,[0-9]{2,3})*(?:\.[0-9]{1,2})?)", text)
        if cgst_m:
            cgst = cls._parse_money(cgst_m.group(1))

        sgst_m = re.search(r"(?i)\b(?:sgst|state\s+gst)\b(?:\s*@\s*\d+(?:\.\d+)?%?)?\s*[:#-]?\s*(?:INR|Rs\.?|₹|\$)?\s*([0-9]+(?:,[0-9]{2,3})*(?:\.[0-9]{1,2})?)", text)
        if sgst_m:
            sgst = cls._parse_money(sgst_m.group(1))

        igst_m = re.search(r"(?i)\b(?:igst|integrated\s+gst)\b(?:\s*@\s*\d+(?:\.\d+)?%?)?\s*[:#-]?\s*(?:INR|Rs\.?|₹|\$)?\s*([0-9]+(?:,[0-9]{2,3})*(?:\.[0-9]{1,2})?)", text)
        if igst_m:
            igst = cls._parse_money(igst_m.group(1))

        return subtotal, taxable_amount, cgst, sgst, igst

    @staticmethod
    def _extract_po_number(text: str) -> Optional[str]:
        po_m = re.search(r"(?i)\b(?:p\.?o\.?\s*(?:number|no\.?|#)|purchase\s+order(?:\s+(?:number|no\.?|#))?|order\s+no\.?)\s*[:#-]?\s*([A-Za-z0-9/_-]{2,40})\b", text)
        return po_m.group(1).strip() if po_m else None

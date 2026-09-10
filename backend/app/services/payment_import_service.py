"""Validation, preview, matching, and idempotent CSV payment ingestion."""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.customer import Customer
from backend.app.schemas.payment import ImportErrorRow, PaymentImportResponse

logger = logging.getLogger(__name__)

HEADER_ALIASES = {
    "invoice": {
        "invoice_number", "invoice_no", "invoice_reference", "invoicenumber",
        "invoiceno", "bill_number", "bill_no", "document_number", "doc_id",
    },
    "date": {"payment_date", "paymentdate", "clear_date", "paid_date", "date_paid"},
    "amount": {"payment_amount", "paymentamount", "amount_paid", "paid_amount", "amount"},
    "reference": {"reference", "payment_reference", "paymentreference", "utr", "transaction_id"},
    "customer": {"customer_reference", "customer_ref", "customer_number", "customer_no", "cust_number"},
}

DATE_FORMATS = (
    "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y",
    "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
)


def _normalize_header(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _find_column(fieldnames: list[str], group: str) -> Optional[str]:
    normalized = {_normalize_header(field): field for field in fieldnames if field}
    for alias in HEADER_ALIASES[group]:
        if alias in normalized:
            return normalized[alias]
    return None


def _normalize_reference(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    return re.sub(r"\s+", "", value).casefold()


def _parse_date(value: str) -> Optional[datetime]:
    cleaned = unicodedata.normalize("NFKC", value or "").strip()
    cleaned = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", cleaned, flags=re.I)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T].*", cleaned):
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        except ValueError:
            pass
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        except ValueError:
            continue
    return None


def _parse_amount(value: str) -> Optional[Decimal]:
    cleaned = unicodedata.normalize("NFKC", value or "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").strip()
    cleaned = re.sub(r"(?i)\b(?:INR|USD|CAD|EUR|GBP|AUD|RS\.?)\b", "", cleaned)
    cleaned = re.sub(r"[₹$€£,\s]", "", cleaned)
    if negative:
        cleaned = "-" + cleaned
    try:
        amount = Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def _decode_csv(file_content: bytes) -> str:
    if not file_content:
        raise HTTPException(status_code=400, detail="Uploaded CSV file is empty.")
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return file_content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="Unable to decode CSV. Save it as UTF-8 and try again.")


def _payment_key(reference: str, payment_date: datetime, amount: Decimal | float) -> tuple[str, str, Decimal]:
    return (
        _normalize_reference(reference),
        payment_date.astimezone(timezone.utc).isoformat(),
        Decimal(str(amount)).quantize(Decimal("0.01")),
    )


def _process_csv(
    db: Session,
    business_id: uuid.UUID,
    file_content: bytes,
    *,
    persist: bool,
) -> PaymentImportResponse:
    reader = csv.DictReader(io.StringIO(_decode_csv(file_content), newline=""))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="Uploaded CSV file contains no headers.")

    inv_col = _find_column(reader.fieldnames, "invoice")
    date_col = _find_column(reader.fieldnames, "date")
    amount_col = _find_column(reader.fieldnames, "amount")
    ref_col = _find_column(reader.fieldnames, "reference")
    customer_col = _find_column(reader.fieldnames, "customer")
    missing = [
        canonical for canonical, found in (
            ("invoice_number", inv_col), ("payment_date", date_col),
            ("payment_amount", amount_col),
        ) if not found
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV is missing required columns: {', '.join(missing)}.",
        )

    invoices = list(db.scalars(select(Invoice).where(Invoice.business_id == business_id)).all())
    invoices_by_ref = {_normalize_reference(inv.invoice_number): inv for inv in invoices}
    invoice_number_by_id = {inv.id: inv.invoice_number for inv in invoices}

    existing_keys = set()
    for payment in db.scalars(select(Payment).where(Payment.business_id == business_id)).all():
        existing_ref = payment.invoice_reference or (
            invoice_number_by_id.get(payment.invoice_id, "") if payment.invoice_id else ""
        )
        existing_keys.add(_payment_key(existing_ref, payment.payment_date, payment.amount))

    file_keys = set()
    payments: list[Payment] = []
    existing_customer_refs = {
        customer.customer_ref.casefold()
        for customer in db.scalars(select(Customer).where(Customer.business_id == business_id)).all()
        if customer.customer_ref
    }
    customers: list[Customer] = []
    invoices_to_mark_paid: set[uuid.UUID] = set()
    errors: list[ImportErrorRow] = []
    total_rows = rejected = duplicate_file = duplicate_existing = unmatched = matched = 0

    for row_number, row in enumerate(reader, start=2):
        total_rows += 1
        raw_invoice = (row.get(inv_col) or "").strip()
        raw_date = (row.get(date_col) or "").strip()
        raw_amount = (row.get(amount_col) or "").strip()

        reason: Optional[str] = None
        parsed_date = _parse_date(raw_date)
        parsed_amount = _parse_amount(raw_amount)
        if not raw_invoice:
            reason = "Missing invoice number/reference."
        elif not parsed_date:
            reason = f"Invalid or unparseable payment date: '{raw_date}'."
        elif parsed_amount is None:
            reason = f"Payment amount must be a positive monetary value: '{raw_amount}'."
        if reason:
            rejected += 1
            errors.append(ImportErrorRow(row_number=row_number, reason=reason, raw_data=dict(row)))
            continue

        key = _payment_key(raw_invoice, parsed_date, parsed_amount)
        if key in file_keys:
            duplicate_file += 1
            continue
        file_keys.add(key)
        if key in existing_keys:
            duplicate_existing += 1
            continue

        matching_invoice = invoices_by_ref.get(_normalize_reference(raw_invoice))
        customer_ref = (row.get(customer_col) or "").strip() if customer_col else ""
        if customer_ref and customer_ref.casefold() not in existing_customer_refs:
            existing_customer_refs.add(customer_ref.casefold())
            customers.append(Customer(
                business_id=business_id,
                customer_ref=customer_ref,
                name=f"Customer {customer_ref}",
            ))
        stored_reference = matching_invoice.invoice_number if matching_invoice else raw_invoice
        if matching_invoice:
            matched += 1
            invoices_to_mark_paid.add(matching_invoice.id)
        else:
            unmatched += 1
        payments.append(Payment(
            business_id=business_id,
            invoice_id=matching_invoice.id if matching_invoice else None,
            invoice_reference=stored_reference,
            payment_date=parsed_date,
            amount=parsed_amount,
            reference=(row.get(ref_col) or "").strip() or None if ref_col else None,
        ))

    if persist:
        try:
            if payments:
                db.add_all(payments)
            if customers:
                db.add_all(customers)
            for invoice_id in invoices_to_mark_paid:
                invoices_by_ref[_normalize_reference(invoice_number_by_id[invoice_id])].payment_status = "PAID"
            db.commit()
        except Exception as e:
            db.rollback()
            logger.exception("Failed to persist payment-history import")
            raise HTTPException(status_code=500, detail="Failed to persist payment records to the database.") from e

    duplicates = duplicate_file + duplicate_existing
    response = PaymentImportResponse(
        total_rows=total_rows,
        valid_rows=total_rows - rejected,
        imported=len(payments),
        matched=matched,
        duplicates=duplicates,
        duplicates_in_file=duplicate_file,
        duplicates_existing=duplicate_existing,
        rejected=rejected,
        unmatched=unmatched,
        preview=not persist,
        errors=errors[:50],
    )
    logger.info(
        "Payment CSV %s: total=%d imported=%d matched=%d unmatched=%d file_duplicates=%d database_duplicates=%d rejected=%d",
        "import" if persist else "preview", total_rows, len(payments), matched, unmatched,
        duplicate_file, duplicate_existing, rejected,
    )
    return response


def preview_payments_csv(db: Session, business_id: uuid.UUID, file_content: bytes) -> PaymentImportResponse:
    return _process_csv(db, business_id, file_content, persist=False)


def import_payments_csv(db: Session, business_id: uuid.UUID, file_content: bytes) -> PaymentImportResponse:
    return _process_csv(db, business_id, file_content, persist=True)

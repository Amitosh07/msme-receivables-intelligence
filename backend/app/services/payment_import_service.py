"""
Payment CSV ingestion service.
Handles CSV parsing, column mapping, date/amount validation,
deterministic duplicate prevention, and invoice/customer matching.
"""

import csv
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.schemas.payment import ImportErrorRow, PaymentImportResponse

logger = logging.getLogger(__name__)

# Standard column synonyms for flexible canonical CSV ingestion
INVOICE_COLUMNS = ["invoice_number", "invoice_reference", "doc_id", "invoice_id"]
PAYMENT_DATE_COLUMNS = ["payment_date", "clear_date", "date"]
AMOUNT_COLUMNS = ["payment_amount", "amount", "total_open_amount"]
REFERENCE_COLUMNS = ["reference", "payment_reference", "ref", "utr"]
CUSTOMER_COLUMNS = ["customer_reference", "cust_number", "customer_id"]

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
]


def _find_column(fieldnames: List[str], candidates: List[str]) -> Optional[str]:
    """Find matching field name case-insensitively from candidates."""
    normalized = {f.strip().lower().replace(" ", "_"): f for f in fieldnames if f}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _parse_date(date_str: str) -> Optional[datetime]:
    """Attempt to parse date string across supported canonical date formats."""
    cleaned = date_str.strip()
    # If date contains fractional seconds (e.g. .000000), strip them
    if "." in cleaned and " " in cleaned:
        cleaned = cleaned.split(".")[0]
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def import_payments_csv(
    db: Session,
    business_id: uuid.UUID,
    file_content: bytes,
) -> PaymentImportResponse:
    """
    Ingests and validates a payment history CSV file for an authenticated business tenant.
    
    Processing Rules:
    1. Validates required canonical columns (invoice identifier, payment date, amount).
    2. Validates row format (date parseable, amount positive numeric).
    3. Handles duplicates deterministically using (invoice_ref, payment_date, amount).
    4. Matches payments against existing invoices; retains unmatched valid payments.
    5. Creates customer record deterministically when customer_reference is present.
    """
    if not file_content or len(file_content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded CSV file is empty.",
        )

    # Decode content
    try:
        text_content = file_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text_content = file_content.decode("latin-1")
        except UnicodeDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to decode CSV file. Please ensure it is UTF-8 encoded.",
            ) from e

    reader = csv.DictReader(io.StringIO(text_content))
    if not reader.fieldnames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded CSV file contains no headers.",
        )

    # Map headers to canonical names
    inv_col = _find_column(reader.fieldnames, INVOICE_COLUMNS)
    date_col = _find_column(reader.fieldnames, PAYMENT_DATE_COLUMNS)
    amount_col = _find_column(reader.fieldnames, AMOUNT_COLUMNS)
    ref_col = _find_column(reader.fieldnames, REFERENCE_COLUMNS)
    cust_col = _find_column(reader.fieldnames, CUSTOMER_COLUMNS)

    missing_cols = []
    if not inv_col:
        missing_cols.append("invoice_number")
    if not date_col:
        missing_cols.append("payment_date")
    if not amount_col:
        missing_cols.append("amount")

    if missing_cols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"CSV is missing required columns: {', '.join(missing_cols)}. "
                f"Accepted headers include: {INVOICE_COLUMNS[0]}, {PAYMENT_DATE_COLUMNS[0]}, {AMOUNT_COLUMNS[0]}"
            ),
        )

    # Preload existing invoices for this tenant for fast lookup
    invoices_map: Dict[str, Invoice] = {
        inv.invoice_number: inv
        for inv in db.scalars(select(Invoice).where(Invoice.business_id == business_id)).all()
    }

    # Preload existing customers for this tenant
    customers_map: Dict[str, Customer] = {
        cust.customer_ref: cust
        for cust in db.scalars(select(Customer).where(Customer.business_id == business_id)).all()
        if cust.customer_ref
    }

    # Preload existing payments for deterministic duplicate detection
    existing_payments_query = db.scalars(
        select(Payment).where(Payment.business_id == business_id)
    ).all()
    seen_keys: Set[Tuple[str, str, float]] = {
        (
            p.invoice_reference or (invoices_map.get(str(p.invoice_id), None) and invoices_map[str(p.invoice_id)].invoice_number) or "",
            p.payment_date.date().isoformat(),
            round(float(p.amount), 2),
        )
        for p in existing_payments_query
    }

    total_rows = 0
    imported = 0
    duplicates = 0
    rejected = 0
    unmatched = 0
    errors: List[ImportErrorRow] = []

    payments_to_add: List[Payment] = []
    new_customers_to_add: List[Customer] = []

    for row_idx, row in enumerate(reader, start=2):  # Row 2 is first data row
        total_rows += 1

        # 1. Invoice reference validation
        raw_inv = row.get(inv_col, "").strip() if inv_col else ""
        if not raw_inv:
            rejected += 1
            errors.append(ImportErrorRow(
                row_number=row_idx,
                reason="Missing invoice number/reference",
                raw_data=dict(row),
            ))
            continue

        # 2. Payment date validation
        raw_date = row.get(date_col, "").strip() if date_col else ""
        parsed_date = _parse_date(raw_date)
        if not parsed_date:
            rejected += 1
            errors.append(ImportErrorRow(
                row_number=row_idx,
                reason=f"Invalid or unparseable payment date: '{raw_date}'",
                raw_data=dict(row),
            ))
            continue

        # 3. Amount validation
        raw_amount = row.get(amount_col, "").strip() if amount_col else ""
        try:
            parsed_amount = float(raw_amount)
            if parsed_amount <= 0:
                rejected += 1
                errors.append(ImportErrorRow(
                    row_number=row_idx,
                    reason=f"Payment amount must be positive, got: {parsed_amount}",
                    raw_data=dict(row),
                ))
                continue
        except (ValueError, TypeError):
            rejected += 1
            errors.append(ImportErrorRow(
                row_number=row_idx,
                reason=f"Invalid numeric amount: '{raw_amount}'",
                raw_data=dict(row),
            ))
            continue

        # 4. Duplicate check (within batch and against database)
        dup_key = (raw_inv, parsed_date.date().isoformat(), round(parsed_amount, 2))
        if dup_key in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(dup_key)

        # 5. Customer reference handling (create customer if not present)
        raw_cust = row.get(cust_col, "").strip() if cust_col else ""
        if raw_cust and raw_cust not in customers_map:
            new_customer = Customer(
                business_id=business_id,
                customer_ref=raw_cust,
                name=f"Customer {raw_cust}",
            )
            customers_map[raw_cust] = new_customer
            new_customers_to_add.append(new_customer)

        # 6. Payment/Invoice matching
        reference_val = row.get(ref_col, "").strip() if ref_col else None

        matching_inv = invoices_map.get(raw_inv)
        if matching_inv:
            payment = Payment(
                business_id=business_id,
                invoice_id=matching_inv.id,
                invoice_reference=raw_inv,
                payment_date=parsed_date,
                amount=parsed_amount,
                reference=reference_val,
            )
            # Mark invoice as paid
            matching_inv.payment_status = "PAID"
            imported += 1
        else:
            # Retain valid payment as unmatched without fabricating an invoice
            payment = Payment(
                business_id=business_id,
                invoice_id=None,
                invoice_reference=raw_inv,
                payment_date=parsed_date,
                amount=parsed_amount,
                reference=reference_val,
            )
            unmatched += 1

        payments_to_add.append(payment)

    # 7. Commit changes transactionally
    try:
        if new_customers_to_add:
            db.add_all(new_customers_to_add)
            db.flush()
        if payments_to_add:
            db.add_all(payments_to_add)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Failed to commit payment batch import: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist payment records to database.",
        ) from e

    logger.info(
        "Payment CSV import complete: total=%d, imported=%d, unmatched=%d, duplicates=%d, rejected=%d",
        total_rows, imported, unmatched, duplicates, rejected,
    )

    return PaymentImportResponse(
        total_rows=total_rows,
        imported=imported,
        duplicates=duplicates,
        rejected=rejected,
        unmatched=unmatched,
        errors=errors[:50],  # Return up to 50 structured errors for response compactness
    )

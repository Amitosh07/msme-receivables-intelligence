"""Manual payment recording service for Phase D."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment

logger = logging.getLogger(__name__)

# V1 operates in India and stores payment facts as a calendar date represented
# by midnight UTC.  A supplied datetime is first interpreted in the V1
# business timezone, so the manual and proof workflows cannot disagree around
# a UTC midnight boundary.  This is intentionally not per-user localisation.
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Kolkata")


def business_today() -> date:
    """Return today's business calendar date for the India-focused V1."""
    return datetime.now(BUSINESS_TIMEZONE).date()


@dataclass(frozen=True)
class ManualPaymentResult:
    """Result of a manual payment recording operation."""
    payment: Payment
    invoice: Invoice
    total_paid: Decimal
    outstanding_balance: Decimal
    payment_status: str
    is_duplicate: bool = False


def _validate_amount(amount: float | Decimal | str) -> Decimal:
    """Validate and normalize payment amount."""
    try:
        parsed = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Payment amount must be a valid number.") from exc
    if not parsed.is_finite():
        raise ValueError("Payment amount must be a finite number.")
    if parsed <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    return parsed.quantize(Decimal("0.01"))


def _validate_payment_date(payment_date: date | datetime) -> datetime:
    """Validate and normalize a factual payment to the V1 business date."""
    if isinstance(payment_date, datetime):
        if payment_date.tzinfo is None:
            factual_date = payment_date.date()
        else:
            factual_date = payment_date.astimezone(BUSINESS_TIMEZONE).date()
    else:
        factual_date = payment_date

    if factual_date > business_today():
        raise ValueError(
            "Payment date cannot be in the future. "
            "A manual payment represents an actual received transaction."
        )
    # Date-only payment facts have no time-of-day semantics in V1.  Persist a
    # stable UTC representation after the business-date check.
    return datetime(
        factual_date.year, factual_date.month, factual_date.day, tzinfo=timezone.utc
    )


def derive_invoice_payment_status(
    db: Session,
    invoice: Invoice,
) -> str:
    """Derive factual payment status from real Payment rows.
    
    Never uses predictions. Status is purely based on actual recorded payments.
    Returns: 'OPEN', 'PARTIAL', or 'PAID'.
    """
    total_paid = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.invoice_id == invoice.id
        )
    ) or Decimal("0")
    
    total_paid = Decimal(str(total_paid))
    invoice_amount = Decimal(str(invoice.amount))
    
    if total_paid <= 0:
        return "OPEN"
    elif total_paid >= invoice_amount:
        return "PAID"
    else:
        return "PARTIAL"


def get_invoice_payment_summary(
    db: Session,
    invoice: Invoice,
) -> tuple[Decimal, Decimal]:
    """Return (total_paid, outstanding_balance) for an invoice."""
    total_paid = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.invoice_id == invoice.id
        )
    ) or Decimal("0")
    
    total_paid = Decimal(str(total_paid))
    invoice_amount = Decimal(str(invoice.amount))
    outstanding = max(invoice_amount - total_paid, Decimal("0"))
    return total_paid, outstanding


def get_invoice_payments(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> List[Payment]:
    """Retrieve all payments for an invoice within the tenant scope."""
    return list(
        db.scalars(
            select(Payment).where(
                Payment.invoice_id == invoice_id,
                Payment.business_id == business_id,
            ).order_by(Payment.payment_date.desc(), Payment.created_at.desc())
        ).all()
    )


def record_manual_payment(
    db: Session,
    *,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
    payment_date: date | datetime,
    amount: float | Decimal | str,
    reference: Optional[str] = None,
    note: Optional[str] = None,
) -> ManualPaymentResult:
    """Record a manual payment against an invoice.
    
    Creates a real Payment row with provenance='manual' and updates
    the invoice's factual payment status based on total recorded payments.
    
    The entire operation is transactional: either both the Payment row
    and the invoice status update succeed, or neither does.
    
    Args:
        db: Database session
        business_id: Authenticated tenant business ID (server-side)
        invoice_id: Target invoice UUID
        payment_date: Date payment was received (must not be future)
        amount: Payment amount (must be > 0)
        reference: Optional payment reference/UTR
        note: Optional note
    
    Returns:
        ManualPaymentResult with payment details and updated invoice state
    
    Raises:
        ValueError: For validation failures
        LookupError: If invoice not found or wrong tenant
    """
    # 1. Validate inputs
    validated_amount = _validate_amount(amount)
    validated_date = _validate_payment_date(payment_date)
    
    # 2. Verify invoice exists and belongs to tenant
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
        )
    )
    if invoice is None:
        raise LookupError("Invoice not found.")
    
    # 3. Build customer_identity_key from the invoice's resolved customer
    customer_identity_key: str | None = None
    if invoice.customer_id is not None:
        customer_identity_key = f"customer:{invoice.customer_id}"
    
    # 4. Clean optional fields
    clean_reference = reference.strip()[:128] if reference else None
    clean_note = note.strip()[:512] if note else None
    
    # 5. Create Payment row
    payment = Payment(
        business_id=business_id,
        invoice_id=invoice.id,
        invoice_reference=invoice.invoice_number,
        payment_date=validated_date,
        amount=validated_amount,
        reference=clean_reference,
        customer_identity_key=customer_identity_key,
        provenance="manual",
        note=clean_note,
    )
    
    # 6. Persist with idempotency protection
    try:
        with db.begin_nested():
            db.add(payment)
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name == "uq_payment_natural_key":
            raise ValueError(
                "A payment with the same date and amount already exists for this invoice. "
                "If this is a separate transaction, use a distinct payment reference."
            ) from exc
        logger.exception(
            "Failed to persist manual payment for invoice %s", invoice_id
        )
        raise RuntimeError(
            "Failed to record payment due to a database error."
        ) from exc
    
    # 7. Derive and update invoice payment status
    new_status = derive_invoice_payment_status(db, invoice)
    invoice.payment_status = new_status
    
    # 8. Compute summary for response
    total_paid, outstanding = get_invoice_payment_summary(db, invoice)
    
    # 9. Commit the transaction
    try:
        db.commit()
        db.refresh(payment)
        db.refresh(invoice)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Failed to commit manual payment for invoice %s", invoice_id
        )
        raise RuntimeError(
            "Failed to record payment due to a database error."
        ) from exc
    
    logger.info(
        "Manual payment recorded: payment_id=%s, invoice_id=%s, amount=%s, "
        "new_status=%s, total_paid=%s",
        payment.id, invoice.id, validated_amount, new_status, total_paid,
    )
    
    return ManualPaymentResult(
        payment=payment,
        invoice=invoice,
        total_paid=total_paid,
        outstanding_balance=outstanding,
        payment_status=new_status,
    )

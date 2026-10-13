"""
Scripted demo path: three paid invoices plus one open invoice, then a score.

Runs the real product path end to end inside the caller's own tenant: a demo
customer, three historical invoices settled through the manual-payment flow,
one current invoice, the three-outcome eligibility gate, and the V1 models.
Dates are relative to today so the story stays the same on any day.

Every record is recognisable (customer_ref marker, DEMO-ST- invoice numbers),
re-running reuses what already exists, and removal deletes only those records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.schemas.demo import (
    DemoRemoveResponse,
    DemoRunResponse,
    DemoStatusResponse,
    DemoStep,
)
from backend.app.schemas.prediction import PredictionResponse
from backend.app.services.customer_balance_service import settlement_delays
from backend.app.services.customer_identity import create_customer, normalize_customer_name
from backend.app.services.explanation_service import explain_invoice_prediction, format_money
from backend.app.services.historical_service import create_manual_historical_invoice
from backend.app.services.manual_payment_service import business_today
from backend.app.services.prediction_service import (
    MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
    evaluate_prediction_eligibility,
    get_prediction_for_invoice,
    predict_for_invoice,
)

DEMO_CUSTOMER_NAME = "Sharma Textiles (Demo)"
DEMO_CUSTOMER_REF = "DEMO-SCRIPTED-PATH"
DEMO_INVOICE_PREFIX = "DEMO-ST-"
OPEN_INVOICE_NUMBER = f"{DEMO_INVOICE_PREFIX}OPEN"


@dataclass(frozen=True)
class PaidInvoiceScript:
    number: str
    amount: Decimal
    issued_days_ago: int
    due_days_ago: int
    paid_days_ago: int


PAID_INVOICES = (
    PaidInvoiceScript(f"{DEMO_INVOICE_PREFIX}001", Decimal("42000.00"), 150, 120, 116),
    PaidInvoiceScript(f"{DEMO_INVOICE_PREFIX}002", Decimal("38500.00"), 120, 90, 83),
    PaidInvoiceScript(f"{DEMO_INVOICE_PREFIX}003", Decimal("45250.00"), 90, 60, 58),
)
OPEN_AMOUNT = Decimal("51800.00")
OPEN_ISSUED_DAYS_AGO = 10
OPEN_TERMS_DAYS = 30


class DemoConflictError(ValueError):
    """Raised when demo names or numbers are already used by real records."""


def _fmt_date(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def _lateness(days: int) -> str:
    if days > 0:
        return f"{days} day{'s' if days != 1 else ''} late"
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} early"
    return "on time"


def _find_demo_customer(db: Session, business_id: uuid.UUID) -> Customer | None:
    return db.scalar(select(Customer).where(
        Customer.business_id == business_id,
        Customer.customer_ref == DEMO_CUSTOMER_REF,
    ))


def _demo_invoice(db: Session, business_id: uuid.UUID, customer: Customer, number: str) -> Invoice | None:
    invoice = db.scalar(select(Invoice).where(
        Invoice.business_id == business_id, Invoice.invoice_number == number
    ))
    if invoice is not None and invoice.customer_id != customer.id:
        raise DemoConflictError(
            f"Invoice number {number} is already used by another customer, so the demo cannot run."
        )
    return invoice


def _ensure_customer(db: Session, business_id: uuid.UUID) -> tuple[Customer, bool]:
    customer = _find_demo_customer(db, business_id)
    if customer is not None:
        return customer, False
    clash = db.scalar(select(Customer).where(
        Customer.business_id == business_id,
        Customer.normalized_name == normalize_customer_name(DEMO_CUSTOMER_NAME),
    ))
    if clash is not None:
        raise DemoConflictError(
            f"A customer named {DEMO_CUSTOMER_NAME} already exists and is not demo data."
        )
    customer = create_customer(
        db, business_id=business_id, display_name=DEMO_CUSTOMER_NAME, customer_ref=DEMO_CUSTOMER_REF
    )
    customer.has_historical_context = True
    db.commit()
    db.refresh(customer)
    return customer, True


def run_demo_path(db: Session, business_id: uuid.UUID, *, today: date | None = None) -> DemoRunResponse:
    today = today or business_today()
    customer, created = _ensure_customer(db, business_id)
    steps = [DemoStep(
        key="customer",
        title="Create a demo customer",
        detail=f"{DEMO_CUSTOMER_NAME} — marked as demo data so it can be removed in one step.",
        created=created,
    )]

    for index, script in enumerate(PAID_INVOICES, start=1):
        invoice = _demo_invoice(db, business_id, customer, script.number)
        due = today - timedelta(days=script.due_days_ago)
        paid_on = today - timedelta(days=script.paid_days_ago)
        if invoice is None:
            invoice, _ = create_manual_historical_invoice(
                db,
                business_id,
                customer.id,
                amount=script.amount,
                due_date=due,
                payment_date=paid_on,
                invoice_number=script.number,
                invoice_date=today - timedelta(days=script.issued_days_ago),
            )
            detail = (
                f"{script.number} · {format_money(float(invoice.amount), 'INR')} due {_fmt_date(due)}, "
                f"paid {_fmt_date(paid_on)} ({_lateness(script.due_days_ago - script.paid_days_ago)})"
            )
            step_created = True
        else:
            detail = f"{script.number} · {format_money(float(invoice.amount), 'INR')} already on record"
            step_created = False
        history = len(settlement_delays(db, business_id, [customer.id]).get(customer.id, []))
        steps.append(DemoStep(
            key=f"paid_{index}",
            title=f"Record paid invoice {index} of {len(PAID_INVOICES)}",
            detail=detail,
            history_count=history,
            created=step_created,
        ))

    open_invoice = _demo_invoice(db, business_id, customer, OPEN_INVOICE_NUMBER)
    open_created = open_invoice is None
    if open_invoice is None:
        issued = today - timedelta(days=OPEN_ISSUED_DAYS_AGO)
        open_invoice = Invoice(
            business_id=business_id,
            customer_id=customer.id,
            invoice_number=OPEN_INVOICE_NUMBER,
            invoice_date=issued,
            due_date=issued + timedelta(days=OPEN_TERMS_DAYS),
            amount=OPEN_AMOUNT,
            currency="INR",
            payment_terms=f"Net {OPEN_TERMS_DAYS}",
            payment_status="OPEN",
            processing_status="PROCESSED",
            origin=InvoiceOrigin.CURRENT.value,
        )
        db.add(open_invoice)
        db.commit()
        db.refresh(open_invoice)
    steps.append(DemoStep(
        key="open",
        title="Add an open invoice to collect",
        detail=(
            f"{OPEN_INVOICE_NUMBER} · {format_money(float(open_invoice.amount), open_invoice.currency)} "
            f"issued {_fmt_date(open_invoice.invoice_date)}, due {_fmt_date(open_invoice.due_date)}"
        ),
        created=open_created,
    ))

    eligibility = evaluate_prediction_eligibility(db, open_invoice)
    steps.append(DemoStep(
        key="gate",
        title="Check payment history",
        detail=(
            f"{eligibility.eligible_history_count} of {MIN_CUSTOMER_HISTORY_FOR_PREDICTION} required "
            f"invoices were fully paid before {_fmt_date(open_invoice.invoice_date)}"
            + (" — an estimate is allowed." if eligibility.prediction_available else " — not enough yet.")
        ),
        history_count=eligibility.eligible_history_count,
        created=False,
    ))

    prediction = predict_for_invoice(db, open_invoice.id, business_id)
    steps.append(DemoStep(
        key="score",
        title="Score the open invoice",
        detail=(
            f"{round(float(prediction.risk_score) * 100)}% chance of paying late "
            f"({(prediction.risk_tier or '').title()} risk); expected payment "
            f"{_fmt_date(prediction.expected_payment_date)}"
        ),
        created=True,
    ))

    return DemoRunResponse(
        customer_id=customer.id,
        customer_name=customer.display_name,
        open_invoice_id=open_invoice.id,
        open_invoice_number=OPEN_INVOICE_NUMBER,
        eligible_history_count=eligibility.eligible_history_count,
        required_history=MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
        steps=steps,
        prediction=PredictionResponse.model_validate(prediction),
        explanation=explain_invoice_prediction(db, business_id, open_invoice.id),
    )


def get_demo_status(db: Session, business_id: uuid.UUID) -> DemoStatusResponse:
    customer = _find_demo_customer(db, business_id)
    if customer is None:
        return DemoStatusResponse(exists=False)
    open_invoice = db.scalar(select(Invoice).where(
        Invoice.business_id == business_id,
        Invoice.customer_id == customer.id,
        Invoice.invoice_number == OPEN_INVOICE_NUMBER,
    ))
    scored = bool(open_invoice and get_prediction_for_invoice(db, business_id, open_invoice.id))
    return DemoStatusResponse(
        exists=True,
        customer_id=customer.id,
        customer_name=customer.display_name,
        open_invoice_id=open_invoice.id if open_invoice else None,
        scored=scored,
    )


def remove_demo_data(db: Session, business_id: uuid.UUID) -> DemoRemoveResponse:
    """Delete the demo customer's DEMO-ST- invoices (with their payments and estimates)."""
    customer = _find_demo_customer(db, business_id)
    if customer is None:
        return DemoRemoveResponse(removed_invoices=0, removed_payments=0, removed_customer=False)
    invoices = list(db.scalars(select(Invoice).where(
        Invoice.business_id == business_id, Invoice.customer_id == customer.id
    )).all())
    demo_invoices = [i for i in invoices if (i.invoice_number or "").startswith(DEMO_INVOICE_PREFIX)]
    removed_payments = sum(len(i.payments) for i in demo_invoices)
    for invoice in demo_invoices:
        db.delete(invoice)
    db.flush()
    removed_customer = len(demo_invoices) == len(invoices)
    if removed_customer:
        db.delete(customer)
    db.commit()
    return DemoRemoveResponse(
        removed_invoices=len(demo_invoices),
        removed_payments=removed_payments,
        removed_customer=removed_customer,
    )

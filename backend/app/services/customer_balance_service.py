"""
Customer-level receivables: current balances plus factual payment behaviour.

Balances use the same CURRENT, remaining-balance rules as the dashboard.
Payment behaviour counts one outcome per fully paid invoice (the payment that
completed it) from the same trusted provenances the prediction gate accepts,
so "3 paid invoices on record" here means the same thing as in the model gate.
Invoices whose customer could not be matched are grouped by the extracted name
and flagged, rather than hidden.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.payment import Payment
from backend.app.schemas.customer import CustomerBalance, CustomerBalancesResponse
from backend.app.services.customer_identity import normalize_customer_name
from backend.app.services.dashboard_service import load_outstanding_invoices
from backend.app.services.manual_payment_service import business_today
from backend.app.services.prediction_service import (
    ELIGIBLE_PAYMENT_PROVENANCE,
    MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
)

RISK_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
ZERO = Decimal("0")


@dataclass
class _Group:
    display_name: str
    customer: Customer | None = None
    outstanding: Decimal = ZERO
    overdue: Decimal = ZERO
    open_count: int = 0
    overdue_count: int = 0
    oldest_days_overdue: int | None = None
    next_due: date | None = None
    next_expected: date | None = None
    risk: str | None = None
    delays: list[int] = field(default_factory=list)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def settlement_delays(db: Session, business_id: uuid.UUID, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[int]]:
    """Days between due date and full settlement for each paid invoice, per customer."""
    if not customer_ids:
        return {}
    rows = db.execute(
        select(Invoice.id, Invoice.customer_id, Invoice.amount, Invoice.due_date, Payment.amount, Payment.payment_date)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            Invoice.business_id == business_id,
            Invoice.customer_id.in_(customer_ids),
            Invoice.due_date.is_not(None),
            Payment.business_id == business_id,
            Payment.provenance.in_(ELIGIBLE_PAYMENT_PROVENANCE),
            Payment.amount > 0,
        )
        .order_by(Payment.payment_date.asc(), Payment.id.asc())
    ).all()
    paid: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    settled: set[uuid.UUID] = set()
    delays: dict[uuid.UUID, list[int]] = defaultdict(list)
    for invoice_id, customer_id, invoice_amount, due_date, amount, paid_at in rows:
        if invoice_id in settled:
            continue
        paid[invoice_id] += Decimal(str(amount))
        if paid[invoice_id] >= Decimal(str(invoice_amount)):
            settled.add(invoice_id)
            paid_on = paid_at.date() if isinstance(paid_at, datetime) else paid_at
            delays[customer_id].append((paid_on - due_date).days)
    return delays


def list_customer_balances(
    db: Session,
    business_id: uuid.UUID,
    *,
    today: date | None = None,
) -> CustomerBalancesResponse:
    today = today or business_today()
    groups: dict[str, _Group] = {}
    currencies: set[str] = set()

    def group_for(invoice: Invoice) -> _Group:
        if invoice.customer is not None:
            key = f"customer:{invoice.customer_id}"
            if key not in groups:
                groups[key] = _Group(invoice.customer.display_name, invoice.customer)
        else:
            raw = invoice.unresolved_customer_name or "Customer not identified"
            key = f"unmatched:{normalize_customer_name(raw) or raw.lower()}"
            if key not in groups:
                groups[key] = _Group(raw)
        return groups[key]

    for item in load_outstanding_invoices(db, business_id):
        invoice = item.invoice
        currencies.add((invoice.currency or "INR").upper())
        group = group_for(invoice)
        group.outstanding += item.outstanding
        group.open_count += 1
        if invoice.due_date is not None:
            days = (today - invoice.due_date).days
            if days > 0:
                group.overdue += item.outstanding
                group.overdue_count += 1
                group.oldest_days_overdue = max(group.oldest_days_overdue or 0, days)
            elif group.next_due is None or invoice.due_date < group.next_due:
                group.next_due = invoice.due_date
        prediction = item.prediction
        if prediction is not None:
            expected = prediction.expected_payment_date
            if expected is not None and expected >= today and (group.next_expected is None or expected < group.next_expected):
                group.next_expected = expected
            if RISK_ORDER.get(prediction.risk_tier or "", 0) > RISK_ORDER.get(group.risk or "", 0):
                group.risk = prediction.risk_tier

    settled_customers = db.scalars(
        select(Customer)
        .join(Invoice, Invoice.customer_id == Customer.id)
        .where(Customer.business_id == business_id, Invoice.origin == InvoiceOrigin.CURRENT.value)
        .distinct()
    ).all()
    for customer in settled_customers:
        groups.setdefault(f"customer:{customer.id}", _Group(customer.display_name, customer))

    customer_ids = [g.customer.id for g in groups.values() if g.customer is not None]
    for customer_id, delays in settlement_delays(db, business_id, customer_ids).items():
        groups[f"customer:{customer_id}"].delays = delays

    total = sum((g.outstanding for g in groups.values()), ZERO)
    rows: list[CustomerBalance] = []
    for group in groups.values():
        settled = len(group.delays)
        rows.append(CustomerBalance(
            customer_id=group.customer.id if group.customer else None,
            display_name=group.display_name,
            matched=group.customer is not None,
            outstanding=_money(group.outstanding),
            overdue=_money(group.overdue),
            share_of_outstanding=round(float(group.outstanding / total), 4) if total else 0.0,
            open_invoice_count=group.open_count,
            overdue_invoice_count=group.overdue_count,
            oldest_days_overdue=group.oldest_days_overdue,
            next_due_date=group.next_due,
            next_expected_payment=group.next_expected,
            highest_risk_tier=group.risk,
            settled_invoice_count=settled,
            on_time_rate=round(sum(1 for d in group.delays if d <= 0) / settled, 4) if settled else None,
            average_days_late=round(sum(group.delays) / settled, 1) if settled else None,
            estimates_ready=settled >= MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
            has_history=bool(group.customer and (group.customer.has_historical_context or settled)),
        ))
    rows.sort(key=lambda r: (-r.outstanding, r.display_name.lower()))
    owing = [r for r in rows if r.outstanding > 0]
    return CustomerBalancesResponse(
        as_of=today,
        currency=next(iter(currencies)) if len(currencies) == 1 else "INR",
        mixed_currency=len(currencies) > 1,
        total_outstanding=_money(total),
        customers_owing=len(owing),
        top_customer_share=owing[0].share_of_outstanding if owing else 0.0,
        top_five_share=round(sum(r.share_of_outstanding for r in owing[:5]), 4),
        required_history=MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
        customers=rows,
    )

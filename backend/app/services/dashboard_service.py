"""
Operational receivables dashboard aggregation.

Only CURRENT invoices that are still being collected (OPEN or PARTIAL) count
toward operational totals; HISTORICAL records exist to teach payment behaviour
and are never money the business can collect this week. All amounts use the
remaining balance (invoice amount minus recorded payments), never the face
amount, so a half-paid invoice contributes only what is still owed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.prediction import PredictionResult
from backend.app.schemas.dashboard import (
    AgingBucketSummary,
    AmountCount,
    DashboardSummaryResponse,
    ExpectedPaymentDay,
    ExpectedPaymentInvoice,
    PriorityInvoice,
)
from backend.app.services.manual_payment_service import business_today, get_paid_totals
from backend.app.services.prediction_service import eligible_predictions

DUE_SOON_DAYS = 7
EXPECTED_WINDOW_DAYS = 30
OUTSTANDING_STATUSES = ("OPEN", "PARTIAL")
AGING_BUCKETS = (
    ("current", "Current"),
    ("1-30", "1–30 days"),
    ("31-60", "31–60 days"),
    ("61-90", "61–90 days"),
    ("90+", "90+ days"),
)


def aging_bucket(days_past_due: int) -> str:
    """Non-overlapping buckets; an invoice due today is still current."""
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "1-30"
    if days_past_due <= 60:
        return "31-60"
    if days_past_due <= 90:
        return "61-90"
    return "90+"


def priority_weight(days_past_due: int | None, risk_score: float | None) -> float:
    """Follow-up weight multiplied by the outstanding balance to rank invoices.

    Urgency grows with lateness (capped at 90 days) and is lower for invoices
    that are only due soon. A late-payment probability, when an eligible
    prediction exists, scales the weight between 0.5x and 1.5x; without one the
    ranking relies on facts alone.
    """
    if days_past_due is None:
        urgency = 0.25
    elif days_past_due > 0:
        urgency = 1 + min(days_past_due, 90) / 30
    elif days_past_due >= -DUE_SOON_DAYS:
        urgency = 0.5 + (DUE_SOON_DAYS + days_past_due) / (2 * DUE_SOON_DAYS)
    else:
        urgency = 0.25
    risk = 0.5 + risk_score if risk_score is not None else 1.0
    return urgency * risk


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _reasons(
    days_past_due: int | None,
    prediction: PredictionResult | None,
    payment_status: str,
) -> list[str]:
    reasons: list[str] = []
    if days_past_due is not None:
        if days_past_due > 0:
            reasons.append(f"{_plural(days_past_due, 'day')} overdue")
        elif days_past_due == 0:
            reasons.append("Due today")
        elif days_past_due >= -DUE_SOON_DAYS:
            reasons.append(f"Due in {_plural(-days_past_due, 'day')}")
    if prediction is not None and prediction.risk_tier == "HIGH":
        reasons.append(f"High late-payment risk ({round(float(prediction.risk_score) * 100)}%)")
    if payment_status == "PARTIAL":
        reasons.append("Partially paid")
    return reasons


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


@dataclass(frozen=True)
class OutstandingInvoice:
    """A CURRENT invoice with money still owed and its eligible estimate, if any."""
    invoice: Invoice
    outstanding: Decimal
    prediction: PredictionResult | None


def load_outstanding_invoices(db: Session, business_id: uuid.UUID) -> list[OutstandingInvoice]:
    """CURRENT OPEN/PARTIAL invoices with a positive remaining balance.

    Predictions are attached only when they pass the same three-outcome gate
    as GET /invoices/{id}/prediction, so no surface shows a hidden estimate.
    """
    invoices = list(
        db.scalars(
            select(Invoice)
            .options(selectinload(Invoice.customer))
            .where(
                Invoice.business_id == business_id,
                Invoice.origin == InvoiceOrigin.CURRENT.value,
                Invoice.processing_status == "PROCESSED",
                Invoice.payment_status.in_(OUTSTANDING_STATUSES),
            )
        ).all()
    )
    invoice_ids = [invoice.id for invoice in invoices]
    paid = get_paid_totals(db, invoice_ids)

    predictions = eligible_predictions(db, business_id, invoice_ids)

    result: list[OutstandingInvoice] = []
    for invoice in invoices:
        outstanding = max(Decimal(str(invoice.amount)) - paid.get(invoice.id, Decimal("0")), Decimal("0"))
        if outstanding > 0:
            result.append(OutstandingInvoice(invoice, outstanding, predictions.get(invoice.id)))
    return result


def build_dashboard_summary(
    db: Session,
    business_id: uuid.UUID,
    *,
    today: date | None = None,
    priority_limit: int = 5,
) -> DashboardSummaryResponse:
    today = today or business_today()

    current_invoice_count = db.scalar(
        select(func.count())
        .select_from(Invoice)
        .where(
            Invoice.business_id == business_id,
            Invoice.origin == InvoiceOrigin.CURRENT.value,
            Invoice.processing_status == "PROCESSED",
        )
    ) or 0

    totals = defaultdict(lambda: [Decimal("0"), 0])
    aging = {key: [Decimal("0"), 0] for key, _ in AGING_BUCKETS}
    schedule: dict[date, list[tuple[Invoice, Decimal]]] = defaultdict(list)
    ranked: list[tuple[float, date, PriorityInvoice]] = []
    currencies: set[str] = set()
    expected_until = today + timedelta(days=EXPECTED_WINDOW_DAYS)

    def add(segment: str, amount: Decimal) -> None:
        totals[segment][0] += amount
        totals[segment][1] += 1

    for item in load_outstanding_invoices(db, business_id):
        invoice, outstanding, prediction = item.invoice, item.outstanding, item.prediction
        currencies.add((invoice.currency or "INR").upper())
        add("outstanding", outstanding)

        days_past_due = (today - invoice.due_date).days if invoice.due_date else None
        if days_past_due is None:
            add("undated", outstanding)
        else:
            bucket = aging[aging_bucket(days_past_due)]
            bucket[0] += outstanding
            bucket[1] += 1
            if days_past_due > 0:
                add("overdue", outstanding)
            elif days_past_due >= -DUE_SOON_DAYS:
                add("due_soon", outstanding)

        risk_score = float(prediction.risk_score) if prediction is not None else None
        is_high_risk = prediction is not None and prediction.risk_tier == "HIGH"
        if is_high_risk:
            add("high_risk", outstanding)

        expected = prediction.expected_payment_date if prediction else None
        if expected is not None and today <= expected <= expected_until:
            add("expected_payments", outstanding)
            schedule[expected].append((invoice, outstanding))

        needs_attention = is_high_risk or (
            days_past_due is not None and days_past_due >= -DUE_SOON_DAYS
        )
        if needs_attention:
            weight = priority_weight(days_past_due, risk_score)
            ranked.append((
                float(outstanding) * weight,
                invoice.due_date or date.max,
                PriorityInvoice(
                    invoice_id=invoice.id,
                    invoice_number=invoice.invoice_number,
                    customer_name=invoice.customer_name,
                    due_date=invoice.due_date,
                    days_past_due=days_past_due,
                    amount=_money(Decimal(str(invoice.amount))),
                    outstanding_balance=_money(outstanding),
                    currency=invoice.currency,
                    payment_status=invoice.payment_status,
                    risk_tier=prediction.risk_tier if prediction else None,
                    risk_score=risk_score,
                    expected_payment_date=expected,
                    reasons=_reasons(days_past_due, prediction, invoice.payment_status),
                ),
            ))

    ranked.sort(key=lambda item: (-item[0], item[1]))

    def segment(name: str) -> AmountCount:
        amount, count = totals[name]
        return AmountCount(amount=_money(amount), count=count)

    return DashboardSummaryResponse(
        as_of=today,
        currency=next(iter(currencies)) if len(currencies) == 1 else "INR",
        mixed_currency=len(currencies) > 1,
        due_soon_days=DUE_SOON_DAYS,
        expected_window_days=EXPECTED_WINDOW_DAYS,
        current_invoice_count=current_invoice_count,
        outstanding=segment("outstanding"),
        overdue=segment("overdue"),
        due_soon=segment("due_soon"),
        high_risk=segment("high_risk"),
        expected_payments=segment("expected_payments"),
        undated=segment("undated"),
        aging=[
            AgingBucketSummary(key=key, label=label, amount=_money(aging[key][0]), count=aging[key][1])
            for key, label in AGING_BUCKETS
        ],
        expected_schedule=[
            ExpectedPaymentDay(
                expected_date=day,
                amount=_money(sum((amount for _, amount in items), Decimal("0"))),
                count=len(items),
                invoices=[
                    ExpectedPaymentInvoice(
                        invoice_id=invoice.id,
                        invoice_number=invoice.invoice_number,
                        customer_name=invoice.customer_name,
                        outstanding_balance=_money(amount),
                    )
                    for invoice, amount in items
                ],
            )
            for day, items in sorted(schedule.items())
        ],
        priority=[item for _, _, item in ranked[:priority_limit]],
    )

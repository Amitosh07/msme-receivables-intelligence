"""
Cash-flow forecast: expected payment date x remaining balance.

Every outstanding CURRENT invoice is placed at most once. The model's predicted
payment date is used when the customer passes the three-outcome history gate;
otherwise the due date stands in, which assumes on-time payment. Balances whose
timing is genuinely unknown (predicted date already passed, overdue without an
estimate, no due date) are reported separately instead of being guessed into a
week. The assumptions returned with the forecast are generated from the same
numbers, so the caveats always describe the forecast actually shown.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import uuid

from sqlalchemy.orm import Session

from backend.app.schemas.dashboard import (
    AmountCount,
    CashflowForecastResponse,
    ForecastInvoice,
    ForecastUnscheduled,
    ForecastWeek,
)
from backend.app.services.dashboard_service import OutstandingInvoice, load_outstanding_invoices
from backend.app.services.explanation_service import format_money, model_quality
from backend.app.services.manual_payment_service import business_today
from backend.app.services.prediction_service import MIN_CUSTOMER_HISTORY_FOR_PREDICTION

DEFAULT_HORIZON_WEEKS = 8
ZERO = Decimal("0")


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _classify(item: OutstandingInvoice, today: date) -> tuple[str, date | None]:
    """Return (basis, expected date) for one outstanding invoice."""
    invoice, prediction = item.invoice, item.prediction
    if prediction is not None and prediction.expected_payment_date is not None:
        if prediction.expected_payment_date >= today:
            return "predicted", prediction.expected_payment_date
        return "past_predicted", prediction.expected_payment_date
    if invoice.due_date is None:
        return "undated", None
    if invoice.due_date >= today:
        return "due_date", invoice.due_date
    return "overdue_unestimated", None


def build_cashflow_forecast(
    db: Session,
    business_id: uuid.UUID,
    *,
    today: date | None = None,
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
) -> CashflowForecastResponse:
    today = today or business_today()
    horizon_end = today + timedelta(days=7 * horizon_weeks - 1)
    week_totals = [defaultdict(lambda: ZERO) for _ in range(horizon_weeks)]
    week_invoices: list[list[ForecastInvoice]] = [[] for _ in range(horizon_weeks)]
    buckets: dict[str, list[ForecastInvoice]] = defaultdict(list)
    bucket_amounts: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_basis: dict[str, list[Decimal]] = defaultdict(list)
    outstanding_total, modelled_total = ZERO, ZERO
    currencies: set[str] = set()

    items = load_outstanding_invoices(db, business_id)
    for item in items:
        invoice, amount = item.invoice, item.outstanding
        currencies.add((invoice.currency or "INR").upper())
        outstanding_total += amount
        if item.prediction is not None:
            modelled_total += amount
        basis, expected = _classify(item, today)
        entry = ForecastInvoice(
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            customer_name=invoice.customer_name,
            outstanding_balance=_money(amount),
            due_date=invoice.due_date,
            expected_date=expected,
            basis=basis,
            risk_tier=item.prediction.risk_tier if item.prediction else None,
        )
        if basis in ("predicted", "due_date"):
            by_basis[basis].append(amount)
            if expected > horizon_end:
                buckets["beyond_horizon"].append(entry)
                bucket_amounts["beyond_horizon"] += amount
                continue
            index = (expected - today).days // 7
            totals = week_totals[index]
            totals["total"] += amount
            totals[basis] += amount
            if entry.risk_tier == "HIGH":
                totals["high_risk"] += amount
            week_invoices[index].append(entry)
        else:
            buckets[basis].append(entry)
            bucket_amounts[basis] += amount

    weeks = [
        ForecastWeek(
            start=today + timedelta(days=7 * i),
            end=today + timedelta(days=7 * i + 6),
            total=_money(week_totals[i]["total"]),
            predicted=_money(week_totals[i]["predicted"]),
            due_date_based=_money(week_totals[i]["due_date"]),
            high_risk=_money(week_totals[i]["high_risk"]),
            count=len(week_invoices[i]),
            invoices=sorted(week_invoices[i], key=lambda e: (e.expected_date, -e.outstanding_balance)),
        )
        for i in range(horizon_weeks)
    ]
    scheduled_amount = sum((Decimal(str(w.total)) for w in weeks), ZERO)
    scheduled_count = sum(w.count for w in weeks)

    unscheduled_meta = [
        ("past_predicted", "Predicted date has passed",
         "The model expected payment by now and none has been recorded. Follow up."),
        ("overdue_unestimated", "Overdue, no estimate",
         "Past the due date, and the customer has too little payment history for an estimate."),
        ("beyond_horizon", f"After {horizon_weeks} weeks",
         f"Expected after {horizon_end.day} {horizon_end.strftime('%b %Y')}."),
        ("undated", "No due date", "Add a due date so these can be forecast."),
    ]
    unscheduled = [
        ForecastUnscheduled(
            key=key,
            label=label,
            detail=detail,
            amount=_money(bucket_amounts[key]),
            count=len(buckets[key]),
            invoices=sorted(buckets[key], key=lambda e: -e.outstanding_balance),
        )
        for key, label, detail in unscheduled_meta
        if buckets[key]
    ]

    currency = next(iter(currencies)) if len(currencies) == 1 else "INR"
    mixed = len(currencies) > 1

    def fmt(value: Decimal) -> str:
        return format_money(float(value), currency)

    predicted_amounts, due_amounts = by_basis["predicted"], by_basis["due_date"]
    quality = model_quality()
    start, end = quality["training_period"].split(" to ")[0][:4], quality["test_period"].split(" to ")[1][:4]
    date_rule = (
        f"The expected date is the model's predicted payment date when the customer has at least "
        f"{MIN_CUSTOMER_HISTORY_FOR_PREDICTION} fully paid invoices on record "
        f"({_plural(len(predicted_amounts), 'invoice')}, {fmt(sum(predicted_amounts, ZERO))}). "
    )
    if due_amounts:
        date_rule += (
            f"Otherwise the due date is used ({_plural(len(due_amounts), 'invoice')}, "
            f"{fmt(sum(due_amounts, ZERO))}). That assumes on-time payment, so it is likely optimistic."
        )
    else:
        date_rule += "No invoice in this forecast relies on its due date."
    assumptions = [
        f"Covers the remaining balance of {_plural(len(items), 'current invoice')} still being collected "
        f"({fmt(outstanding_total)}). Historical records and fully paid invoices are excluded.",
        "Each invoice is assumed to be paid in full, in a single payment, on its expected date. "
        "Part-payments are not forecast.",
        date_rule,
        f"On held-out test data the timing model's typical error was {quality['timing_median_error_days']:.1f} "
        f"days (median) and {quality['timing_mae_days']:.1f} days on average. That data comes from an "
        f"external dataset ({start}–{end}), so errors for this business may be larger.",
        "Late-payment risk is shown for each week but not subtracted. It is the probability of paying "
        "after the due date, not of never paying.",
        "Invoices whose predicted date has passed, overdue invoices without an estimate and invoices "
        "without a due date are listed separately instead of being guessed into a week.",
        f"Weeks are rolling 7-day periods starting {today.day} {today.strftime('%b %Y')} (India business date)."
        + (" Amounts in different currencies are added without conversion." if mixed else ""),
    ]

    return CashflowForecastResponse(
        as_of=today,
        currency=currency,
        mixed_currency=mixed,
        horizon_weeks=horizon_weeks,
        horizon_end=horizon_end,
        outstanding=AmountCount(amount=_money(outstanding_total), count=len(items)),
        scheduled=AmountCount(amount=_money(scheduled_amount), count=scheduled_count),
        model_dated=AmountCount(amount=_money(sum(predicted_amounts, ZERO)), count=len(predicted_amounts)),
        due_date_dated=AmountCount(amount=_money(sum(due_amounts, ZERO)), count=len(due_amounts)),
        model_coverage=round(float(modelled_total / outstanding_total), 4) if outstanding_total else 0.0,
        weeks=weeks,
        unscheduled=unscheduled,
        assumptions=assumptions,
    )

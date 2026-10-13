"""
Explanations for V1 payment estimates.

Per-invoice drivers come from XGBoost's exact additive feature contributions
(TreeSHAP) for the late-payment classifier: the starting point plus every
contribution reproduces the invoice's risk score in log-odds space. The 29 raw
model inputs are grouped into a handful of factors a business owner can act on
or verify, and each factor carries the factual evidence it was computed from.

Global importance and model quality figures come from the trained artifacts
and their evaluation report; they describe the external training data, not
this tenant's history, and are labelled as such.
"""

from __future__ import annotations

from functools import lru_cache
import json
import math
import uuid
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.invoice import Invoice
from backend.app.models.prediction import PredictionResult
from backend.app.schemas.prediction import (
    ExplanationFactor,
    ImportanceFactor,
    ModelInsightsResponse,
    PredictionExplanationResponse,
)
from backend.app.services.prediction_service import (
    CLASSIFIER_MODEL_VERSION,
    MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
    MODEL_DIR,
    TIMING_MODEL_VERSION,
    PredictionServiceError,
    build_inference_features,
    evaluate_prediction_eligibility,
    get_predictor,
)

FACTOR_GROUPS: Dict[str, tuple[str, List[str]]] = {
    "late_history": (
        "Customer's late-payment record",
        ["cust_late_payment_rate", "cust_prior_late_count", "cust_recent_late_rate_3"],
    ),
    "typical_delay": (
        "How late the customer usually pays",
        [
            "cust_avg_delay",
            "cust_median_delay",
            "cust_std_delay",
            "cust_max_delay",
            "cust_min_delay",
            "cust_recent_avg_delay_3",
        ],
    ),
    "history_depth": (
        "Length and recency of payment history",
        [
            "cust_prior_invoice_count",
            "cust_prior_payment_count",
            "is_new_customer",
            "days_since_last_payment",
            "days_since_prev_invoice",
        ],
    ),
    "credit_terms": (
        "Credit period",
        ["term_duration_days", "days_until_due_at_posting", "payment_terms"],
    ),
    "due_date_timing": (
        "When the due date falls",
        ["due_month", "due_day_of_week", "is_weekend_due"],
    ),
    "issue_timing": (
        "When the invoice was issued",
        [
            "posting_month",
            "posting_day_of_month",
            "posting_day_of_week",
            "posting_quarter",
            "is_weekend_posting",
        ],
    ),
    "amount": ("Invoice amount", ["amount", "log_amount"]),
    "account_context": ("Account and currency codes", ["business_code", "currency"]),
}
FEATURE_TO_GROUP = {
    feature: key for key, (_, features) in FACTOR_GROUPS.items() for feature in features
}
CATEGORY_LABELS = {
    "currency": "currency",
    "business_code": "business code",
    "payment_terms": "payment-terms code",
}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
RISK_TIER_BANDS = {"LOW": "below 30%", "MEDIUM": "30% to 65%", "HIGH": "65% or higher"}
METHOD_NOTE = (
    "Each factor's effect is its exact contribution to this invoice's score "
    "(XGBoost feature contributions, also known as TreeSHAP). The starting point "
    "plus all effects equals the estimate."
)


class ExplanationUnavailableError(PredictionServiceError):
    """Raised when an invoice has no estimate that can be explained."""


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _relative_to_due(days: float) -> str:
    rounded = int(round(days))
    if rounded > 0:
        return f"{_plural(rounded, 'day')} after the due date"
    if rounded < 0:
        return f"{_plural(-rounded, 'day')} before the due date"
    return "on the due date"


def format_money(amount: float, currency: str | None) -> str:
    code = (currency or "INR").upper()
    prefix = "₹" if code == "INR" else f"{code} "
    return f"{prefix}{amount:,.2f}"


def _evidence(group: str, f: Dict[str, Any], invoice: Invoice, unknown: List[str]) -> str:
    settled = int(f["cust_prior_payment_count"])
    if group == "late_history":
        late = int(f["cust_prior_late_count"])
        recent_window = min(settled, 3)
        recent_late = int(round(float(f["cust_recent_late_rate_3"]) * recent_window))
        text = f"Paid late on {late} of {_plural(settled, 'settled invoice')}"
        if settled > recent_window:
            text += f", including {recent_late} of the last {recent_window}"
        return text
    if group == "typical_delay":
        return (
            f"Usually pays {_relative_to_due(f['cust_median_delay'])} (median); "
            f"slowest was {_relative_to_due(f['cust_max_delay'])}"
        )
    if group == "history_depth":
        return (
            f"{_plural(settled, 'settled invoice')} before this one; last payment "
            f"{_plural(int(f['days_since_last_payment']), 'day')} before the invoice date"
        )
    if group == "credit_terms":
        text = f"{_plural(int(f['term_duration_days']), 'day')} from invoice date to due date"
        return text + (f" (terms: {invoice.payment_terms})" if invoice.payment_terms else "")
    if group == "due_date_timing":
        due = invoice.due_date
        weekend = " — a weekend" if due.weekday() >= 5 else ""
        return f"Due on {WEEKDAYS[due.weekday()]}, {due.day} {due.strftime('%b %Y')}{weekend}"
    if group == "issue_timing":
        issued = invoice.invoice_date
        return f"Issued on {WEEKDAYS[issued.weekday()]}, {issued.day} {issued.strftime('%b %Y')}"
    if group == "amount":
        return format_money(float(f["amount"]), invoice.currency)
    unseen = [CATEGORY_LABELS[c] for c in unknown if c in ("currency", "business_code")]
    if unseen:
        return (
            f"This {' and '.join(unseen)} did not appear in the model's training data, "
            "so it is treated as unknown"
        )
    return "Business code and currency as seen in the training data"


def explain_invoice_prediction(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> PredictionExplanationResponse:
    """Explain the stored, eligible estimate for one tenant-owned invoice."""
    invoice = db.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.business_id == business_id)
    )
    if invoice is None:
        raise PredictionServiceError(f"Invoice {invoice_id} not found.")
    prediction = db.scalar(
        select(PredictionResult).where(
            PredictionResult.business_id == business_id,
            PredictionResult.invoice_id == invoice.id,
        )
    )
    if prediction is None or not evaluate_prediction_eligibility(db, invoice).prediction_available:
        raise ExplanationUnavailableError("No estimate is available for this invoice yet.")

    features = build_inference_features(db, invoice)
    predictor = get_predictor()
    contributions = predictor.explain(features).iloc[0]
    unknown = predictor.unknown_categories(features)
    row = features.iloc[0].to_dict()

    bias = float(contributions["bias"])
    grouped: Dict[str, float] = {key: 0.0 for key in FACTOR_GROUPS}
    for feature, value in contributions.drop("bias").items():
        grouped[FEATURE_TO_GROUP[feature]] += float(value)
    total_effect = sum(abs(v) for v in grouped.values()) or 1.0

    factors = [
        ExplanationFactor(
            key=key,
            label=FACTOR_GROUPS[key][0],
            effect="raises" if value > 0 else "lowers",
            contribution=round(value, 4),
            share=round(abs(value) / total_effect, 4),
            evidence=_evidence(key, row, invoice, unknown),
        )
        for key, value in sorted(grouped.items(), key=lambda kv: -abs(kv[1]))
    ]
    current_risk = _sigmoid(bias + sum(grouped.values()))
    stored_risk = float(prediction.risk_score)
    return PredictionExplanationResponse(
        invoice_id=invoice.id,
        risk_score=round(current_risk, 4),
        stored_risk_score=stored_risk,
        matches_stored=abs(current_risk - stored_risk) < 0.005,
        risk_tier=prediction.risk_tier,
        baseline_risk=round(_sigmoid(bias), 4),
        factors=factors,
        unknown_categories=[CATEGORY_LABELS[c] for c in unknown],
        method=METHOD_NOTE,
    )


@lru_cache(maxsize=1)
def _evaluation_report() -> Dict[str, Any]:
    with open(MODEL_DIR / "evaluation_report.json", "r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _model_metadata() -> Dict[str, Any]:
    with open(MODEL_DIR / "model_metadata.json", "r", encoding="utf-8") as fh:
        return json.load(fh)


def model_quality() -> Dict[str, Any]:
    """Held-out test metrics and training provenance from the model artifacts."""
    report = _evaluation_report()
    clf_meta = _model_metadata()["models"]["classifier"]
    clf = report["classifier"]["test_metrics"]
    timing = report["timing_model"]["test_metrics"]
    return {
        "roc_auc": float(clf["roc_auc"]),
        "accuracy": float(clf["accuracy"]),
        "precision": float(clf["precision"]),
        "recall": float(clf["recall"]),
        "timing_mae_days": float(timing["mae"]),
        "timing_median_error_days": float(timing["median_absolute_error"]),
        "training_period": clf_meta["training_period"],
        "test_period": clf_meta["test_period"],
        "training_rows": int(clf_meta["training_rows"]),
    }


def _grouped_importance(importance: Dict[str, float]) -> List[ImportanceFactor]:
    totals: Dict[str, float] = {key: 0.0 for key in FACTOR_GROUPS}
    for feature, value in importance.items():
        totals[FEATURE_TO_GROUP[feature]] += value
    return [
        ImportanceFactor(key=key, label=FACTOR_GROUPS[key][0], importance=round(value, 4))
        for key, value in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


def get_model_insights() -> ModelInsightsResponse:
    importance = get_predictor().feature_importance()
    quality = model_quality()
    start, end = quality["training_period"].split(" to ")
    return ModelInsightsResponse(
        classifier_model_version=CLASSIFIER_MODEL_VERSION,
        timing_model_version=TIMING_MODEL_VERSION,
        risk_drivers=_grouped_importance(importance["classifier"]),
        timing_drivers=_grouped_importance(importance["timing"]),
        roc_auc=quality["roc_auc"],
        accuracy=quality["accuracy"],
        precision=quality["precision"],
        recall=quality["recall"],
        timing_mae_days=quality["timing_mae_days"],
        timing_median_error_days=quality["timing_median_error_days"],
        training_rows=quality["training_rows"],
        training_period=quality["training_period"],
        test_period=quality["test_period"],
        required_history=MIN_CUSTOMER_HISTORY_FOR_PREDICTION,
        risk_tiers=RISK_TIER_BANDS,
        limitations=[
            f"Trained on {quality['training_rows']:,} invoices from an external B2B dataset "
            f"({start[:4]}–{end[:4]}), not on this business's own records.",
            "Rupee (INR) invoices, local payment-terms text and new business codes were not in "
            "the training data, so the model treats them as unknown categories.",
            f"An estimate needs at least {MIN_CUSTOMER_HISTORY_FOR_PREDICTION} invoices from the "
            "same customer that were fully paid before the new invoice's date.",
            "Importance shows what the model relies on overall. It reflects patterns in the "
            "training data, not proven causes of late payment.",
        ],
    )

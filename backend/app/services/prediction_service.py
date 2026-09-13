"""
Prediction and feature engineering service for Phase 6.
Constructs leakage-safe as-of customer features from application database records,
invokes the trained V1 ML inference models, and persists prediction results idempotently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

import numpy as np
import pandas as pd
from sqlalchemy import Date, and_, func, or_, select
from sqlalchemy.orm import Session

from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice, InvoiceOrigin
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.ml.inference.predict import InferenceError, V1Predictor

logger = logging.getLogger(__name__)

MIN_CUSTOMER_HISTORY_FOR_PREDICTION = 3
INSUFFICIENT_HISTORY_REASON = "Insufficient customer payment history"
ELIGIBLE_PAYMENT_PROVENANCE = frozenset(
    {"legacy", "import", "manual", "proof_verified"}
)
CLASSIFIER_MODEL_VERSION = "payment_classifier_v1"
TIMING_MODEL_VERSION = "payment_timing_v1"

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODEL_DIR = BASE_DIR / "ml" / "models"

# Exact 29 feature columns expected by V1Predictor
FEATURE_COLUMNS: List[str] = [
    "amount",
    "log_amount",
    "term_duration_days",
    "days_until_due_at_posting",
    "posting_month",
    "posting_day_of_month",
    "posting_day_of_week",
    "posting_quarter",
    "is_weekend_posting",
    "due_month",
    "due_day_of_week",
    "is_weekend_due",
    "cust_prior_invoice_count",
    "cust_prior_payment_count",
    "cust_prior_late_count",
    "cust_late_payment_rate",
    "cust_avg_delay",
    "cust_median_delay",
    "cust_std_delay",
    "cust_max_delay",
    "cust_min_delay",
    "cust_recent_avg_delay_3",
    "cust_recent_late_rate_3",
    "days_since_last_payment",
    "days_since_prev_invoice",
    "is_new_customer",
    "business_code",
    "currency",
    "payment_terms",
]

# Prohibited target/leakage columns that must NEVER reach the feature matrix
PROHIBITED_COLUMNS = {
    "clear_date",
    "isOpen",
    "delay_days",
    "is_late",
    "days_until_payment",
}

# Frozen training-partition median statistics for cold-start customer imputation
COLD_START_IMPUTATION: Dict[str, float] = {
    "cust_late_payment_rate": 0.329609,
    "cust_avg_delay": 0.170732,
    "cust_median_delay": 0.0,
    "cust_std_delay": 4.036694,
    "cust_max_delay": 15.0,
    "cust_min_delay": -7.0,
    "cust_recent_avg_delay_3": 0.0,
    "cust_recent_late_rate_3": 0.333333,
    "days_since_last_payment": 2.0,
    "days_since_prev_invoice": 1.0,
}

# In-memory cached predictor instance
_cached_predictor: Optional[V1Predictor] = None


def get_predictor() -> V1Predictor:
    """Return in-memory cached V1Predictor singleton."""
    global _cached_predictor
    if _cached_predictor is None:
        logger.info("Initializing V1Predictor from: %s", MODEL_DIR)
        _cached_predictor = V1Predictor(model_dir=MODEL_DIR).load()
    return _cached_predictor


class PredictionServiceError(Exception):
    """Base exception for application prediction service failures."""
    pass


class InvoiceNotReadyError(PredictionServiceError):
    """Raised when an invoice cannot be scored because it is incomplete or failed."""
    pass


class InsufficientCustomerHistoryError(PredictionServiceError):
    """Expected business state when fewer than three prior outcomes exist."""

    def __init__(self, invoice_id: uuid.UUID, eligible_history_count: int):
        super().__init__(INSUFFICIENT_HISTORY_REASON)
        self.invoice_id = invoice_id
        self.eligible_history_count = eligible_history_count
        self.required_history_count = MIN_CUSTOMER_HISTORY_FOR_PREDICTION


@dataclass(frozen=True)
class PredictionEligibility:
    """Eligibility evidence derived from tenant-scoped PostgreSQL history."""

    invoice_id: uuid.UUID
    customer_id: uuid.UUID | None
    eligible_history_count: int

    @property
    def prediction_available(self) -> bool:
        return self.eligible_history_count >= MIN_CUSTOMER_HISTORY_FOR_PREDICTION


def get_eligible_prior_payments(db: Session, invoice: Invoice) -> list[Payment]:
    """Return one factual completion outcome per historical invoice, as known at T."""
    if invoice.customer_id is None or invoice.invoice_date is None:
        return []
    candidates = list(
        db.scalars(
            select(Payment)
            .join(Invoice, Payment.invoice_id == Invoice.id)
            .where(
                Payment.business_id == invoice.business_id,
                Payment.provenance.in_(ELIGIBLE_PAYMENT_PROVENANCE),
                Payment.amount > 0,
                Invoice.business_id == invoice.business_id,
                Invoice.customer_id == invoice.customer_id,
                Invoice.origin == InvoiceOrigin.HISTORICAL.value,
                Invoice.due_date.is_not(None),
                or_(
                    Invoice.invoice_date < invoice.invoice_date,
                    and_(
                        Invoice.invoice_date.is_(None),
                        Invoice.due_date < invoice.invoice_date,
                    ),
                ),
                Invoice.id != invoice.id,
                func.cast(Payment.payment_date, Date) < invoice.invoice_date,
            )
            .order_by(Payment.payment_date.asc(), Payment.id.asc())
        ).all()
    )

    # Payment rows are factual events, but the V1 threshold is expressed in
    # completed invoice outcomes. Accumulate eligible linked payments and emit
    # only the payment that first settles each invoice. This prevents an unpaid
    # shell or several partial payments on one invoice from inflating history.
    paid_by_invoice: dict[uuid.UUID, float] = {}
    completed_invoice_ids: set[uuid.UUID] = set()
    completed_outcomes: list[Payment] = []
    for payment in candidates:
        invoice_id = payment.invoice_id
        if invoice_id is None or invoice_id in completed_invoice_ids:
            continue
        paid_by_invoice[invoice_id] = (
            paid_by_invoice.get(invoice_id, 0.0) + float(payment.amount)
        )
        if paid_by_invoice[invoice_id] >= float(payment.invoice.amount):
            completed_invoice_ids.add(invoice_id)
            completed_outcomes.append(payment)
    return completed_outcomes


def evaluate_prediction_eligibility(
    db: Session,
    invoice: Invoice,
) -> PredictionEligibility:
    """Evaluate the locked three-outcome rule for one invoice."""
    return PredictionEligibility(
        invoice_id=invoice.id,
        customer_id=invoice.customer_id,
        eligible_history_count=len(get_eligible_prior_payments(db, invoice)),
    )


def get_prediction_eligibility(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> PredictionEligibility:
    """Load one tenant-owned invoice and evaluate its history threshold."""
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.business_id == business_id,
        )
    )
    if invoice is None:
        raise PredictionServiceError(f"Invoice {invoice_id} not found.")
    return evaluate_prediction_eligibility(db, invoice)


def validate_invoice_for_scoring(invoice: Optional[Invoice]) -> None:
    """Ensure invoice exists, is not in ERROR processing state, and has required data."""
    if not invoice:
        raise InvoiceNotReadyError("Invoice does not exist.")

    if invoice.processing_status != "PROCESSED":
        raise InvoiceNotReadyError(
            f"Cannot generate prediction until invoice processing is complete (current status: {invoice.processing_status})."
        )

    if invoice.amount is None or float(invoice.amount) <= 0:
        raise InvoiceNotReadyError(
            f"Cannot generate prediction: invoice {invoice.id} has invalid or non-positive amount."
        )

    if not invoice.invoice_date or not invoice.due_date:
        raise InvoiceNotReadyError(
            f"Cannot generate prediction: invoice {invoice.id} is missing invoice_date or due_date."
        )


def build_inference_features(db: Session, invoice: Invoice) -> pd.DataFrame:
    """
    Constructs a 1-row DataFrame containing all 29 features expected by V1 models.
    All customer history features are computed strictly as-of the invoice's invoice_date (T).
    No future payment outcomes, eventual clear dates, or target labels are ever included.
    """
    validate_invoice_for_scoring(invoice)

    ref_date: date = invoice.invoice_date
    business = db.get(Business, invoice.business_id)
    business_code = (business.business_code or "U001") if business else "U001"

    # 1. Query prior invoices of this customer strictly before ref_date
    prior_invoices = list(
        db.scalars(
            select(Invoice)
            .where(
                Invoice.business_id == invoice.business_id,
                Invoice.customer_id == invoice.customer_id,
                Invoice.origin == InvoiceOrigin.HISTORICAL.value,
                Invoice.invoice_date < ref_date,
                Invoice.id != invoice.id,
            )
            .order_by(Invoice.invoice_date.asc())
        ).all()
    )

    cust_prior_invoice_count = len(prior_invoices)
    if cust_prior_invoice_count > 0:
        prev_inv_date = max(inv.invoice_date for inv in prior_invoices)
        days_since_prev_invoice = float(max(0, (ref_date - prev_inv_date).days))
    else:
        days_since_prev_invoice = COLD_START_IMPUTATION["days_since_prev_invoice"]

    # 2. Reuse the exact eligible completed-outcome query used by the gate.
    prior_payments = get_eligible_prior_payments(db, invoice)

    cust_prior_payment_count = len(prior_payments)

    if cust_prior_payment_count > 0:
        delays: List[float] = []
        lates: List[float] = []
        payment_dates: List[date] = []

        for p in prior_payments:
            p_date = p.payment_date.date() if isinstance(p.payment_date, datetime) else p.payment_date
            payment_dates.append(p_date)
            p_due = p.invoice.due_date
            p_delay = float((p_date - p_due).days)
            delays.append(p_delay)
            lates.append(1.0 if p_delay > 0 else 0.0)

        cust_prior_late_count = int(sum(lates))
        cust_late_payment_rate = float(cust_prior_late_count / cust_prior_payment_count)
        cust_avg_delay = float(np.mean(delays))
        cust_median_delay = float(np.median(delays))
        cust_std_delay = float(np.std(delays, ddof=1)) if cust_prior_payment_count >= 2 else 0.0
        cust_max_delay = float(np.max(delays))
        cust_min_delay = float(np.min(delays))

        # Recent 3 payments
        recent_delays = delays[-3:]
        recent_lates = lates[-3:]
        cust_recent_avg_delay_3 = float(np.mean(recent_delays))
        cust_recent_late_rate_3 = float(np.mean(recent_lates))

        last_pay_date = max(payment_dates)
        days_since_last_payment = float(max(0, (ref_date - last_pay_date).days))

    else:
        # Cold start imputation
        cust_prior_late_count = 0
        cust_late_payment_rate = COLD_START_IMPUTATION["cust_late_payment_rate"]
        cust_avg_delay = COLD_START_IMPUTATION["cust_avg_delay"]
        cust_median_delay = COLD_START_IMPUTATION["cust_median_delay"]
        cust_std_delay = COLD_START_IMPUTATION["cust_std_delay"]
        cust_max_delay = COLD_START_IMPUTATION["cust_max_delay"]
        cust_min_delay = COLD_START_IMPUTATION["cust_min_delay"]
        cust_recent_avg_delay_3 = COLD_START_IMPUTATION["cust_recent_avg_delay_3"]
        cust_recent_late_rate_3 = COLD_START_IMPUTATION["cust_recent_late_rate_3"]
        days_since_last_payment = COLD_START_IMPUTATION["days_since_last_payment"]

    is_new_customer = 1 if cust_prior_invoice_count < 3 else 0

    # 3. Invoice-intrinsic features
    amount_val = float(invoice.amount)
    log_amount = float(np.log1p(max(0.0, amount_val)))
    term_duration_days = int((invoice.due_date - invoice.invoice_date).days)
    days_until_due_at_posting = int((invoice.due_date - invoice.invoice_date).days)

    posting_month = int(ref_date.month)
    posting_day_of_month = int(ref_date.day)
    posting_day_of_week = int(ref_date.weekday())
    posting_quarter = int((ref_date.month - 1) // 3 + 1)
    is_weekend_posting = int(1 if posting_day_of_week >= 5 else 0)

    due_month = int(invoice.due_date.month)
    due_day_of_week = int(invoice.due_date.weekday())
    is_weekend_due = int(1 if due_day_of_week >= 5 else 0)

    currency = invoice.currency or "USD"
    payment_terms = invoice.payment_terms or "NAA8"

    feature_dict: Dict[str, Any] = {
        "amount": amount_val,
        "log_amount": log_amount,
        "term_duration_days": term_duration_days,
        "days_until_due_at_posting": days_until_due_at_posting,
        "posting_month": posting_month,
        "posting_day_of_month": posting_day_of_month,
        "posting_day_of_week": posting_day_of_week,
        "posting_quarter": posting_quarter,
        "is_weekend_posting": is_weekend_posting,
        "due_month": due_month,
        "due_day_of_week": due_day_of_week,
        "is_weekend_due": is_weekend_due,
        "cust_prior_invoice_count": cust_prior_invoice_count,
        "cust_prior_payment_count": cust_prior_payment_count,
        "cust_prior_late_count": cust_prior_late_count,
        "cust_late_payment_rate": cust_late_payment_rate,
        "cust_avg_delay": cust_avg_delay,
        "cust_median_delay": cust_median_delay,
        "cust_std_delay": cust_std_delay,
        "cust_max_delay": cust_max_delay,
        "cust_min_delay": cust_min_delay,
        "cust_recent_avg_delay_3": cust_recent_avg_delay_3,
        "cust_recent_late_rate_3": cust_recent_late_rate_3,
        "days_since_last_payment": days_since_last_payment,
        "days_since_prev_invoice": days_since_prev_invoice,
        "is_new_customer": is_new_customer,
        "business_code": business_code,
        "currency": currency,
        "payment_terms": payment_terms,
    }

    df = pd.DataFrame([feature_dict])[FEATURE_COLUMNS]

    # Enforce zero target leakage
    leaked = PROHIBITED_COLUMNS.intersection(set(df.columns))
    if leaked:
        raise InferenceError(f"Target leakage detected in feature row: {sorted(leaked)}")

    return df


def predict_for_invoice(
    db: Session,
    invoice_id: uuid.UUID,
    business_id: uuid.UUID,
) -> PredictionResult:
    """
    Executes V1 ML prediction for an invoice and idempotently persists the result.
    Enforces tenant isolation: invoice must belong to business_id.
    """
    # Lock the invoice row so concurrent retry/click requests converge on one
    # prediction record even before the prediction row exists.
    invoice = db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.business_id == business_id)
        .with_for_update()
    )
    if not invoice:
        raise PredictionServiceError(f"Invoice {invoice_id} not found.")

    validate_invoice_for_scoring(invoice)

    eligibility = evaluate_prediction_eligibility(db, invoice)
    logger.info(
        "Prediction eligibility: invoice_id=%s customer_id=%s "
        "eligible_history_count=%d prediction_available=%s "
        "classifier=%s timing=%s",
        invoice.id,
        invoice.customer_id,
        eligibility.eligible_history_count,
        eligibility.prediction_available,
        CLASSIFIER_MODEL_VERSION,
        TIMING_MODEL_VERSION,
    )
    if not eligibility.prediction_available:
        raise InsufficientCustomerHistoryError(
            invoice.id,
            eligibility.eligible_history_count,
        )

    # 1. Build 29-feature vector
    features_df = build_inference_features(db, invoice)

    # 2. Run V1Predictor
    predictor = get_predictor()
    scored_df = predictor.predict(features_df)

    row = scored_df.iloc[0]
    risk_score = float(np.clip(float(row["risk_score"]), 0.0, 1.0))
    is_late_pred = bool(int(row["is_late_predicted"]) == 1)
    risk_tier = str(row["risk_tier"])
    predicted_days = float(max(0.0, float(row["predicted_days_until_payment"])))

    # Derived expected payment calendar date = invoice_date + predicted days
    expected_payment_date: date = invoice.invoice_date + timedelta(days=int(round(predicted_days)))

    # 3. Idempotently update or create PredictionResult record
    pred = db.scalar(
        select(PredictionResult).where(
            PredictionResult.business_id == business_id,
            PredictionResult.invoice_id == invoice.id,
        )
    )

    if pred:
        pred.prediction = is_late_pred
        pred.risk_score = round(risk_score, 4)
        pred.risk_tier = risk_tier
        pred.predicted_days_until_payment = round(predicted_days, 2)
        pred.expected_payment_date = expected_payment_date
        pred.classifier_model_version = CLASSIFIER_MODEL_VERSION
        pred.timing_model_version = TIMING_MODEL_VERSION
        logger.info("Updated existing prediction for invoice %s (risk=%s, tier=%s)", invoice.id, risk_score, risk_tier)
    else:
        pred = PredictionResult(
            id=uuid.uuid4(),
            business_id=business_id,
            invoice_id=invoice.id,
            prediction=is_late_pred,
            risk_score=round(risk_score, 4),
            risk_tier=risk_tier,
            predicted_days_until_payment=round(predicted_days, 2),
            expected_payment_date=expected_payment_date,
            classifier_model_version=CLASSIFIER_MODEL_VERSION,
            timing_model_version=TIMING_MODEL_VERSION,
        )
        db.add(pred)
        logger.info("Created new prediction for invoice %s (risk=%s, tier=%s)", invoice.id, risk_score, risk_tier)

    db.commit()
    db.refresh(pred)
    return pred


def get_prediction_for_invoice(
    db: Session,
    business_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> Optional[PredictionResult]:
    """Retrieve the prediction for an invoice, enforcing tenant isolation."""
    prediction = db.scalar(
        select(PredictionResult).where(
            PredictionResult.business_id == business_id,
            PredictionResult.invoice_id == invoice_id,
        )
    )
    if prediction is None:
        return None
    if not evaluate_prediction_eligibility(db, prediction.invoice).prediction_available:
        return None
    return prediction


def list_predictions_for_tenant(
    db: Session,
    business_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
    risk_tier: Optional[str] = None,
) -> Tuple[List[PredictionResult], int]:
    """List all predictions for a tenant with optional risk_tier filtering."""
    query = select(PredictionResult).where(PredictionResult.business_id == business_id)
    if risk_tier:
        query = query.where(PredictionResult.risk_tier == risk_tier.upper())

    persisted_results = list(
        db.scalars(
            query.order_by(PredictionResult.created_at.desc())
        ).all()
    )
    eligible_results = [
        prediction
        for prediction in persisted_results
        if evaluate_prediction_eligibility(db, prediction.invoice).prediction_available
    ]
    total = len(eligible_results)
    return eligible_results[skip : skip + limit], total

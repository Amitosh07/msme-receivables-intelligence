"""Auditable cleanup for prediction rows invalid under the Phase C history gate."""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.customer import Customer
from backend.app.models.invoice import Invoice
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.app.services.prediction_service import evaluate_prediction_eligibility


@dataclass(frozen=True)
class AmbiguousPrediction:
    prediction_id: str
    reason: str


@dataclass
class PredictionCleanupReport:
    total_before: int
    eligible_ids: list[uuid.UUID] = field(default_factory=list)
    ineligible_ids: list[uuid.UUID] = field(default_factory=list)
    ambiguous: list[AmbiguousPrediction] = field(default_factory=list)
    deleted: int = 0
    total_after: int | None = None
    source_counts_before: dict[str, int] = field(default_factory=dict)
    source_counts_after: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "total_before": self.total_before,
            "eligible": len(self.eligible_ids),
            "ineligible": len(self.ineligible_ids),
            "ambiguous": len(self.ambiguous),
            "ambiguous_rows": [asdict(row) for row in self.ambiguous],
            "deleted": self.deleted,
            "total_after": self.total_after,
            "source_counts_before": self.source_counts_before,
            "source_counts_after": self.source_counts_after,
        }


def _tenant_filter(model: type, business_id: uuid.UUID | None):
    if business_id is None:
        return None
    return model.business_id == business_id


def _count(db: Session, model: type, business_id: uuid.UUID | None) -> int:
    query = select(func.count()).select_from(model)
    condition = _tenant_filter(model, business_id)
    if condition is not None:
        query = query.where(condition)
    return int(db.scalar(query) or 0)


def _source_counts(db: Session, business_id: uuid.UUID | None) -> dict[str, int]:
    return {
        "invoices": _count(db, Invoice, business_id),
        "payments": _count(db, Payment, business_id),
        "customers": _count(db, Customer, business_id),
    }


def audit_prediction_results(
    db: Session,
    *,
    business_id: uuid.UUID | None = None,
    lock_rows: bool = False,
) -> PredictionCleanupReport:
    """Classify prediction rows without changing any database record."""
    query = select(PredictionResult).order_by(PredictionResult.id)
    if business_id is not None:
        query = query.where(PredictionResult.business_id == business_id)
    if lock_rows:
        query = query.with_for_update()
    predictions = list(db.scalars(query).all())
    report = PredictionCleanupReport(
        total_before=len(predictions),
        source_counts_before=_source_counts(db, business_id),
    )

    for prediction in predictions:
        invoice = db.get(Invoice, prediction.invoice_id)
        if invoice is None:
            report.ambiguous.append(
                AmbiguousPrediction(str(prediction.id), "Associated invoice is missing")
            )
            continue
        if invoice.business_id != prediction.business_id:
            report.ambiguous.append(
                AmbiguousPrediction(str(prediction.id), "Prediction/invoice tenant mismatch")
            )
            continue
        if invoice.customer_id is not None:
            customer = db.get(Customer, invoice.customer_id)
            if customer is None or customer.business_id != prediction.business_id:
                report.ambiguous.append(
                    AmbiguousPrediction(
                        str(prediction.id),
                        "Resolved customer is missing or belongs to another tenant",
                    )
                )
                continue
        try:
            eligibility = evaluate_prediction_eligibility(db, invoice)
        except Exception as exc:
            report.ambiguous.append(
                AmbiguousPrediction(
                    str(prediction.id),
                    f"Eligibility evaluation failed: {type(exc).__name__}",
                )
            )
            continue
        destination = (
            report.eligible_ids
            if eligibility.prediction_available
            else report.ineligible_ids
        )
        destination.append(prediction.id)

    return report


def remove_invalid_prediction_results(
    db: Session,
    *,
    business_id: uuid.UUID | None = None,
) -> PredictionCleanupReport:
    """Delete only verified-ineligible predictions in one guarded transaction."""
    report = audit_prediction_results(
        db,
        business_id=business_id,
        lock_rows=True,
    )
    if report.ineligible_ids:
        result = db.execute(
            delete(PredictionResult).where(
                PredictionResult.id.in_(report.ineligible_ids)
            )
        )
        report.deleted = int(result.rowcount or 0)
    db.flush()
    report.total_after = _count(db, PredictionResult, business_id)
    report.source_counts_after = _source_counts(db, business_id)
    if report.source_counts_after != report.source_counts_before:
        db.rollback()
        raise RuntimeError("Source-table counts changed; prediction cleanup was rolled back")
    if report.deleted != len(report.ineligible_ids):
        db.rollback()
        raise RuntimeError("Deletion count did not match the verified ineligible set")
    db.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete verified-ineligible prediction rows; default is read-only.",
    )
    args = parser.parse_args()
    with SessionLocal() as db:
        report = (
            remove_invalid_prediction_results(db)
            if args.apply
            else audit_prediction_results(db)
        )
        if not args.apply:
            report.total_after = report.total_before
            report.source_counts_after = report.source_counts_before.copy()
        print(json.dumps(report.summary(), indent=2))


if __name__ == "__main__":
    main()

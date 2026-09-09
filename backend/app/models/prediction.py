"""
Prediction Result entity model for persisting ML inference outputs.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.models.business import Business
    from backend.app.models.invoice import Invoice


class PredictionResult(Base):
    """
    Stores payment delay classification and timing regression predictions.
    Maps directly to outputs from Phase 2 V1 models.
    """
    __tablename__ = "prediction_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prediction: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    risk_score: Mapped[float] = mapped_column(
        Numeric(5, 4),
        nullable=False,
    )
    risk_tier: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        index=True,
    )
    predicted_days_until_payment: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    expected_payment_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    classifier_model_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default="payment_classifier_v1",
    )
    timing_model_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default="payment_timing_v1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="predictions")
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="predictions")

    def __repr__(self) -> str:
        return (
            f"<PredictionResult(id={self.id}, invoice_id={self.invoice_id}, "
            f"risk_score={self.risk_score}, prediction={self.prediction})>"
        )

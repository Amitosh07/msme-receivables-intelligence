"""
Cashflow Forecast entity model.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING
from sqlalchemy import Date, DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.models.business import Business


class CashflowForecast(Base):
    """
    Represents an aggregated cashflow prediction for a given target date.
    Maintains confidence intervals (lower_bound, upper_bound).
    """
    __tablename__ = "cashflow_forecasts"

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
    forecast_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    predicted_amount: Mapped[float] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )
    lower_bound: Mapped[float | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
    )
    upper_bound: Mapped[float | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="forecasts")

    def __repr__(self) -> str:
        return f"<CashflowForecast(id={self.id}, date={self.forecast_date}, amount={self.predicted_amount})>"

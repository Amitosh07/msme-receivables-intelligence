"""
Payment entity model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.models.business import Business
    from backend.app.models.invoice import Invoice


class Payment(Base):
    """
    Represents a payment remittance clearing an invoice.
    Scoped to business tenant and linked directly to an invoice.
    """
    __tablename__ = "payments"

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
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    invoice_reference: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    payment_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    amount: Mapped[float] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )
    reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="payments")
    invoice: Mapped[Optional[Invoice]] = relationship("Invoice", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment(id={self.id}, invoice_id={self.invoice_id}, amount={self.amount})>"

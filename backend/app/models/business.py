"""
Business (Tenant) entity model.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, List
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.membership import Membership
    from backend.app.models.customer import Customer
    from backend.app.models.invoice import Invoice
    from backend.app.models.payment import Payment
    from backend.app.models.invoice_document import InvoiceDocument
    from backend.app.models.prediction import PredictionResult
    from backend.app.models.forecast import CashflowForecast
    from backend.app.models.task import Task
    from backend.app.models.payment_proof import PaymentProof


class Business(Base, TimestampMixin):
    """
    Represents an MSME business tenant in the multi-tenant architecture.
    All operational domain data is scoped to a business tenant.
    """
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    business_code: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )

    # Relationships
    memberships: Mapped[List[Membership]] = relationship(
        "Membership",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    customers: Mapped[List[Customer]] = relationship(
        "Customer",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    invoices: Mapped[List[Invoice]] = relationship(
        "Invoice",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    payments: Mapped[List[Payment]] = relationship(
        "Payment",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    invoice_documents: Mapped[List[InvoiceDocument]] = relationship(
        "InvoiceDocument",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    predictions: Mapped[List[PredictionResult]] = relationship(
        "PredictionResult",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    forecasts: Mapped[List[CashflowForecast]] = relationship(
        "CashflowForecast",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[List[Task]] = relationship(
        "Task",
        back_populates="business",
        cascade="all, delete-orphan",
    )
    payment_proofs: Mapped[List[PaymentProof]] = relationship(
        "PaymentProof",
        back_populates="business",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Business(id={self.id}, name='{self.name}')>"

"""
Invoice entity model.
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TimestampMixin


class InvoiceOrigin(str, Enum):
    HISTORICAL = "HISTORICAL"
    CURRENT = "CURRENT"

if TYPE_CHECKING:
    from backend.app.models.business import Business
    from backend.app.models.customer import Customer
    from backend.app.models.invoice_document import InvoiceDocument
    from backend.app.models.payment import Payment
    from backend.app.models.payment_proof import PaymentProof
    from backend.app.models.prediction import PredictionResult
    from backend.app.models.task import Task


class Invoice(Base, TimestampMixin):
    """
    Represents a commercial invoice billed to a customer.
    Maintains payment_status ('OPEN', 'PAID') separately from
    processing_status ('PENDING', 'PROCESSING', 'PROCESSED', 'ERROR').
    """
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("business_id", "invoice_number", name="uq_business_invoice_number"),
        CheckConstraint(
            "customer_id IS NULL OR unresolved_customer_name IS NULL",
            name="ck_invoice_customer_resolution",
        ),
        CheckConstraint(
            "origin IN ('HISTORICAL', 'CURRENT')",
            name="ck_invoices_origin",
        ),
        Index("ix_invoices_business_origin", "business_id", "origin"),
    )

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
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    unresolved_customer_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    invoice_number: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    invoice_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    due_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    amount: Mapped[float] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )
    payment_terms: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    payment_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="OPEN",
    )
    processing_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
    )
    origin: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=InvoiceOrigin.CURRENT.value,
        server_default=InvoiceOrigin.CURRENT.value,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice_documents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="invoices")
    customer: Mapped[Customer | None] = relationship("Customer", back_populates="invoices")
    payments: Mapped[list[Payment]] = relationship(
        "Payment",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    predictions: Mapped[list[PredictionResult]] = relationship(
        "PredictionResult",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    documents: Mapped[list[InvoiceDocument]] = relationship(
        "InvoiceDocument",
        back_populates="invoice",
        foreign_keys="[InvoiceDocument.invoice_id]",
        cascade="all, delete-orphan",
    )
    document: Mapped[InvoiceDocument | None] = relationship(
        "InvoiceDocument",
        foreign_keys=[document_id],
        post_update=True,
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    payment_proofs: Mapped[list[PaymentProof]] = relationship(
        "PaymentProof",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Invoice(id={self.id}, number='{self.invoice_number}', "
            f"payment_status='{self.payment_status}', processing_status='{self.processing_status}')>"
        )

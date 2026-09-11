"""
Payment Proof entity model for tracking uploaded payment proof documents and verification state.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional
from sqlalchemy import BigInteger, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.business import Business
    from backend.app.models.invoice import Invoice
    from backend.app.models.payment import Payment
    from backend.app.models.task import Task


class PaymentProof(Base, TimestampMixin):
    """
    Metadata and verification lifecycle for uploaded payment proofs (PDF, CSV, XLSX).
    Maintains link to stored file, target invoice, background task, and verified payment.
    """
    __tablename__ = "payment_proofs"

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
    storage_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    # SHA-256 digest of the uploaded bytes.  This is an integrity/evidence
    # identifier; it is deliberately not described as encryption.
    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
        index=True,
    )
    extracted_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="payment_proofs")
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="payment_proofs")
    payment: Mapped[Optional[Payment]] = relationship("Payment")
    task: Mapped[Optional[Task]] = relationship("Task")

    def __repr__(self) -> str:
        return f"<PaymentProof(id={self.id}, filename='{self.original_filename}', status='{self.status}')>"

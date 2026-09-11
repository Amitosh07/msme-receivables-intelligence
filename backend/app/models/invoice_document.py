"""
Invoice Document entity model for tracking uploaded document metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base
from backend.app.models.invoice import InvoiceOrigin

if TYPE_CHECKING:
    from backend.app.models.business import Business
    from backend.app.models.invoice import Invoice


class InvoiceDocument(Base):
    """
    Metadata for uploaded invoice documents (e.g. PDFs).
    Maintains link to storage key and processing lifecycle.
    """
    __tablename__ = "invoice_documents"
    __table_args__ = (
        CheckConstraint(
            "origin IN ('HISTORICAL', 'CURRENT')",
            name="ck_invoice_documents_origin",
        ),
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
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
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
        String(64),
        nullable=False,
        default="application/pdf",
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
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
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="invoice_documents")
    invoice: Mapped[Optional[Invoice]] = relationship(
        "Invoice",
        back_populates="documents",
        foreign_keys=[invoice_id],
    )

    def __repr__(self) -> str:
        return f"<InvoiceDocument(id={self.id}, filename='{self.original_filename}', status='{self.processing_status}')>"

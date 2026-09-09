"""
Customer entity model.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, List
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.business import Business
    from backend.app.models.invoice import Invoice


class Customer(Base, TimestampMixin):
    """
    Represents a B2B buyer or client owing receivables to a business tenant.
    Scoped to a business tenant via business_id.
    """
    __tablename__ = "customers"

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
    customer_ref: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="customers")
    invoices: Mapped[List[Invoice]] = relationship(
        "Invoice",
        back_populates="customer",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Customer(id={self.id}, name='{self.name}', business_id={self.business_id})>"

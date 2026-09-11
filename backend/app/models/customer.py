"""
Customer entity model.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym, validates

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
    __table_args__ = (
        Index(
            "uq_customers_business_gstin",
            "business_id",
            "normalized_gstin",
            unique=True,
            postgresql_where=text("normalized_gstin IS NOT NULL"),
        ),
        Index(
            "ix_customers_business_normalized_name",
            "business_id",
            "normalized_name",
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
    customer_ref: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    normalized_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    gstin: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    normalized_gstin: Mapped[str | None] = mapped_column(
        String(15),
        nullable=True,
    )

    # Compatibility for existing application code while display_name remains
    # the sole persisted, user-facing name.
    name = synonym("display_name")

    # Relationships
    business: Mapped[Business] = relationship("Business", back_populates="customers")
    invoices: Mapped[list[Invoice]] = relationship(
        "Invoice",
        back_populates="customer",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Customer(id={self.id}, display_name='{self.display_name}', "
            f"business_id={self.business_id})>"
        )

    def __init__(self, **kwargs: object) -> None:
        if "name" in kwargs and "display_name" not in kwargs:
            kwargs["display_name"] = kwargs.pop("name")
        display_name = kwargs.get("display_name")
        if display_name and not kwargs.get("normalized_name"):
            from backend.app.services.customer_identity import normalize_customer_name

            kwargs["normalized_name"] = normalize_customer_name(str(display_name))
        gstin = kwargs.get("gstin")
        if gstin and not kwargs.get("normalized_gstin"):
            from backend.app.services.customer_identity import normalize_gstin

            kwargs["normalized_gstin"] = normalize_gstin(str(gstin))
        super().__init__(**kwargs)

    @validates("display_name")
    def _keep_normalized_name_in_sync(self, _key: str, value: str) -> str:
        from backend.app.services.customer_identity import normalize_customer_name

        self.normalized_name = normalize_customer_name(value)
        return value

    @validates("gstin")
    def _keep_normalized_gstin_in_sync(self, _key: str, value: str | None) -> str | None:
        from backend.app.services.customer_identity import normalize_gstin

        self.normalized_gstin = normalize_gstin(value)
        return value

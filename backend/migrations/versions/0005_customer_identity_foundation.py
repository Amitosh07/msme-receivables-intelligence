"""Add tenant-scoped customer identity and unresolved invoice support.

Revision ID: 0005_customer_identity
Revises: 0004_harden_processing_state
Create Date: 2026-09-11 04:00:00.000000
"""

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_customer_identity"
down_revision: str | None = "0004_harden_processing_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[.,;:()\[\]{}]+", " ", normalized)
    tokens = re.sub(r"\s+", " ", normalized.strip()).casefold().split()
    if len(tokens) >= 2 and tokens[-2:] in (
        ["pvt", "ltd"],
        ["pvt", "limited"],
        ["private", "ltd"],
    ):
        tokens[-2:] = ["private", "limited"]
    return " ".join(tokens)


def upgrade() -> None:
    op.alter_column(
        "customers",
        "name",
        existing_type=sa.String(length=255),
        existing_nullable=False,
        new_column_name="display_name",
    )
    op.add_column(
        "customers",
        sa.Column("normalized_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "customers",
        sa.Column("gstin", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "customers",
        sa.Column("normalized_gstin", sa.String(length=15), nullable=True),
    )

    customers = sa.table(
        "customers",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("display_name", sa.String(length=255)),
        sa.column("normalized_name", sa.String(length=255)),
    )
    connection = op.get_bind()
    for customer_id, display_name in connection.execute(
        sa.select(customers.c.id, customers.c.display_name)
    ):
        connection.execute(
            customers.update()
            .where(customers.c.id == customer_id)
            .values(normalized_name=_normalize_name(display_name))
        )

    op.alter_column(
        "customers",
        "normalized_name",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_index(
        "ix_customers_business_normalized_name",
        "customers",
        ["business_id", "normalized_name"],
        unique=False,
    )
    op.create_index(
        "uq_customers_business_gstin",
        "customers",
        ["business_id", "normalized_gstin"],
        unique=True,
        postgresql_where=sa.text("normalized_gstin IS NOT NULL"),
    )

    op.alter_column(
        "invoices",
        "customer_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "invoices",
        sa.Column("unresolved_customer_name", sa.String(length=255), nullable=True),
    )
    op.create_check_constraint(
        "ck_invoice_customer_resolution",
        "invoices",
        "customer_id IS NULL OR unresolved_customer_name IS NULL",
    )


def downgrade() -> None:
    # Refuse to fabricate customer identities during downgrade.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM invoices WHERE customer_id IS NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade while unresolved invoices exist';
            END IF;
        END $$
        """
    )
    op.drop_constraint("ck_invoice_customer_resolution", "invoices", type_="check")
    op.drop_column("invoices", "unresolved_customer_name")
    op.alter_column(
        "invoices",
        "customer_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index("uq_customers_business_gstin", table_name="customers")
    op.drop_index("ix_customers_business_normalized_name", table_name="customers")
    op.drop_column("customers", "normalized_gstin")
    op.drop_column("customers", "gstin")
    op.drop_column("customers", "normalized_name")
    op.alter_column(
        "customers",
        "display_name",
        existing_type=sa.String(length=255),
        existing_nullable=False,
        new_column_name="name",
    )

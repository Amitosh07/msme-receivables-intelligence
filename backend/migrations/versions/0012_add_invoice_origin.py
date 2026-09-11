"""Add explicit historical vs current data origin to invoices and invoice_documents.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add origin columns as nullable initially
    op.add_column(
        "invoices",
        sa.Column("origin", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "invoice_documents",
        sa.Column("origin", sa.String(length=16), nullable=True),
    )

    # 2. Backfill existing records deterministically:
    # Prior to Phase H, all invoices and documents were ingested through the standard
    # operational/current flow. Deterministically assign 'CURRENT' to pre-existing rows.
    op.execute("UPDATE invoices SET origin = 'CURRENT' WHERE origin IS NULL")
    op.execute("UPDATE invoice_documents SET origin = 'CURRENT' WHERE origin IS NULL")

    # 3. Alter columns to NOT NULL with server_default='CURRENT'
    op.alter_column(
        "invoices",
        "origin",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="CURRENT",
    )
    op.alter_column(
        "invoice_documents",
        "origin",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="CURRENT",
    )

    # 4. Create check constraints to ensure valid origin values
    op.create_check_constraint(
        "ck_invoices_origin",
        "invoices",
        "origin IN ('HISTORICAL', 'CURRENT')",
    )
    op.create_check_constraint(
        "ck_invoice_documents_origin",
        "invoice_documents",
        "origin IN ('HISTORICAL', 'CURRENT')",
    )

    # 5. Create index on (business_id, origin) for tenant-scoped querying
    op.create_index(
        "ix_invoices_business_origin",
        "invoices",
        ["business_id", "origin"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_business_origin", table_name="invoices")
    op.drop_constraint("ck_invoice_documents_origin", "invoice_documents", type_="check")
    op.drop_constraint("ck_invoices_origin", "invoices", type_="check")
    op.drop_column("invoice_documents", "origin")
    op.drop_column("invoices", "origin")

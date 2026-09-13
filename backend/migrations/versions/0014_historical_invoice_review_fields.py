"""Allow incomplete historical invoices to persist for manual review.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column, type_ in (
        ("invoice_number", sa.String(length=64)),
        ("invoice_date", sa.Date()),
        ("due_date", sa.Date()),
        ("currency", sa.String(length=3)),
    ):
        op.alter_column("invoices", column, existing_type=type_, nullable=True)


def downgrade() -> None:
    for column in ("invoice_number", "invoice_date", "due_date", "currency"):
        op.execute(
            sa.text(
                f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM invoices WHERE {column} IS NULL) "
                f"THEN RAISE EXCEPTION 'Cannot downgrade while {column} is missing'; END IF; END $$"
            )
        )
    for column, type_ in (
        ("invoice_number", sa.String(length=64)),
        ("invoice_date", sa.Date()),
        ("due_date", sa.Date()),
        ("currency", sa.String(length=3)),
    ):
        op.alter_column("invoices", column, existing_type=type_, nullable=False)

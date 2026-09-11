"""Persist payment provenance for historical imports.

Revision ID: 0007_add_payment_provenance
Revises: 0006_widen_payment_natural_key
Create Date: 2026-09-11 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007_add_payment_provenance"
down_revision: str | None = "0006_widen_payment_natural_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows remain NULL: their origin is not known with certainty.
    op.add_column(
        "payments",
        sa.Column("provenance", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "provenance")

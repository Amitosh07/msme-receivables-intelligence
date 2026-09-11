"""Backfill explicit provenance for payments created before Phase B.

Revision ID: 0008_backfill_payment_provenance
Revises: 0007_add_payment_provenance
Create Date: 2026-09-11 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0008_backfill_payment_provenance"
down_revision: str | None = "0007_add_payment_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRACKING_TABLE = "_migration_0008_payment_provenance_legacy"


def upgrade() -> None:
    """Record and backfill only rows whose provenance is currently NULL."""
    op.create_table(
        _TRACKING_TABLE,
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
    )
    bind = op.get_bind()
    bind.execute(sa.text(
        f"""
        INSERT INTO {_TRACKING_TABLE} (payment_id)
        SELECT id
        FROM payments
        WHERE provenance IS NULL
        """
    ))
    bind.execute(sa.text(
        f"""
        UPDATE payments AS payment
        SET provenance = 'legacy'
        FROM {_TRACKING_TABLE} AS tracked
        WHERE payment.id = tracked.payment_id
          AND payment.provenance IS NULL
        """
    ))

    remaining_nulls = bind.scalar(
        sa.text("SELECT count(*) FROM payments WHERE provenance IS NULL")
    )
    if remaining_nulls:
        raise RuntimeError(
            "Payment provenance backfill did not complete; "
            f"{remaining_nulls} NULL row(s) remain."
        )

    # Enforce the completed data-hygiene contract only after the verified backfill.
    op.alter_column(
        "payments",
        "provenance",
        existing_type=sa.String(length=32),
        nullable=False,
    )


def downgrade() -> None:
    """Revert only tracked rows that still retain this migration's value."""
    op.alter_column(
        "payments",
        "provenance",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    bind = op.get_bind()
    bind.execute(sa.text(
        f"""
        UPDATE payments AS payment
        SET provenance = NULL
        FROM {_TRACKING_TABLE} AS tracked
        WHERE payment.id = tracked.payment_id
          AND payment.provenance = 'legacy'
        """
    ))
    op.drop_table(_TRACKING_TABLE)

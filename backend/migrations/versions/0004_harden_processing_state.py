"""Harden document errors and prediction idempotency.

Revision ID: 0004_harden_processing_state
Revises: 0003_add_risk_tier
Create Date: 2026-09-10 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_harden_processing_state"
down_revision: Union[str, None] = "0003_add_risk_tier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invoice_documents",
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    # Old application versions did not enforce this at the database boundary.
    # Retain the newest prediction before adding the idempotency constraint.
    op.execute(
        """
        DELETE FROM prediction_results older
        USING prediction_results newer
        WHERE older.business_id = newer.business_id
          AND older.invoice_id = newer.invoice_id
          AND (
              older.created_at < newer.created_at
              OR (older.created_at = newer.created_at AND older.id < newer.id)
          )
        """
    )
    op.create_unique_constraint(
        "uq_prediction_business_invoice",
        "prediction_results",
        ["business_id", "invoice_id"],
    )
    op.execute(
        """
        DELETE FROM payments older
        USING payments newer
        WHERE older.business_id = newer.business_id
          AND lower(btrim(coalesce(older.invoice_reference, ''))) =
              lower(btrim(coalesce(newer.invoice_reference, '')))
          AND older.payment_date = newer.payment_date
          AND older.amount = newer.amount
          AND older.id < newer.id
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_payment_natural_key
        ON payments (
            business_id,
            lower(btrim(coalesce(invoice_reference, ''))),
            payment_date,
            amount
        )
        """
    )


def downgrade() -> None:
    op.drop_index("uq_payment_natural_key", table_name="payments")
    op.drop_constraint(
        "uq_prediction_business_invoice",
        "prediction_results",
        type_="unique",
    )
    op.drop_column("invoice_documents", "error_message")

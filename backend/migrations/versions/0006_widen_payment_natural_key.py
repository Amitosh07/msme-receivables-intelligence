"""Widen the payment natural key with persisted customer identity.

Revision ID: 0006_widen_payment_natural_key
Revises: 0005_customer_identity
Create Date: 2026-09-11 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_widen_payment_natural_key"
down_revision: str | None = "0005_customer_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CANONICAL_INDEX = "uq_payment_natural_key"
_STAGED_INDEX = "uq_payment_natural_key_v2"
_OLD_STAGED_INDEX = "uq_payment_natural_key_v1_restore"


def _validate_unique_index(index_name: str) -> None:
    """Abort the transaction unless PostgreSQL reports a valid unique index."""
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_index i
                    JOIN pg_class c ON c.oid = i.indexrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = current_schema()
                      AND c.relname = '{index_name}'
                      AND i.indisunique
                      AND i.indisvalid
                      AND i.indisready
                ) THEN
                    RAISE EXCEPTION 'Unique index % is not valid and ready', '{index_name}';
                END IF;
            END $$
            """
        )
    )


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("customer_identity_key", sa.String(length=320), nullable=True),
    )

    # A linked invoice with a resolved customer is the only defensible legacy
    # customer identity. Existing unmatched payments deliberately remain NULL.
    op.execute(
        """
        UPDATE payments AS payment
        SET customer_identity_key = 'customer:' || invoice.customer_id::text
        FROM invoices AS invoice
        WHERE payment.invoice_id = invoice.id
          AND invoice.customer_id IS NOT NULL
          AND payment.customer_identity_key IS NULL
        """
    )

    # Keep the old index enforced until the widened replacement has been built
    # and PostgreSQL has explicitly confirmed that it is valid and ready.
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_STAGED_INDEX}
        ON payments (
            business_id,
            customer_identity_key,
            lower(btrim(coalesce(invoice_reference, ''))),
            payment_date,
            amount
        )
        """
    )
    _validate_unique_index(_STAGED_INDEX)
    op.drop_index(_CANONICAL_INDEX, table_name="payments")
    op.execute(f"ALTER INDEX {_STAGED_INDEX} RENAME TO {_CANONICAL_INDEX}")


def downgrade() -> None:
    # Recreate and validate the narrower index before removing the widened one.
    # If widened-key data conflicts under the old definition, CREATE UNIQUE
    # INDEX fails and the transactional migration leaves the new schema intact.
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_OLD_STAGED_INDEX}
        ON payments (
            business_id,
            lower(btrim(coalesce(invoice_reference, ''))),
            payment_date,
            amount
        )
        """
    )
    _validate_unique_index(_OLD_STAGED_INDEX)
    op.drop_index(_CANONICAL_INDEX, table_name="payments")
    op.execute(f"ALTER INDEX {_OLD_STAGED_INDEX} RENAME TO {_CANONICAL_INDEX}")
    op.drop_column("payments", "customer_identity_key")

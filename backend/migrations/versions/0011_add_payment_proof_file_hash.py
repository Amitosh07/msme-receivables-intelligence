"""Add SHA-256 integrity hash to payment-proof metadata.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"


def upgrade() -> None:
    op.add_column("payment_proofs", sa.Column("file_hash", sa.String(64), nullable=True))
    # Existing proof files predate the hash field. Preserve their metadata and
    # mark an empty legacy digest rather than claiming an unverifiable value.
    op.execute("UPDATE payment_proofs SET file_hash = '' WHERE file_hash IS NULL")
    op.alter_column("payment_proofs", "file_hash", nullable=False)
    op.create_index("ix_payment_proofs_file_hash", "payment_proofs", ["file_hash"])


def downgrade() -> None:
    op.drop_index("ix_payment_proofs_file_hash", table_name="payment_proofs")
    op.drop_column("payment_proofs", "file_hash")

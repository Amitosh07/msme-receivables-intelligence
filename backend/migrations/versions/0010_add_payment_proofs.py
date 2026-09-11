"""Add payment_proofs table for Phase E proof verification.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0010"
down_revision = "0009"


def upgrade() -> None:
    op.create_table(
        "payment_proofs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "business_id",
            UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("extracted_data", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "payment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("payments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_payment_proofs_business_id", "payment_proofs", ["business_id"]
    )
    op.create_index(
        "ix_payment_proofs_invoice_id", "payment_proofs", ["invoice_id"]
    )
    op.create_index("ix_payment_proofs_status", "payment_proofs", ["status"])
    op.create_index(
        "ix_payment_proofs_payment_id", "payment_proofs", ["payment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_payment_proofs_payment_id", table_name="payment_proofs")
    op.drop_index("ix_payment_proofs_status", table_name="payment_proofs")
    op.drop_index("ix_payment_proofs_invoice_id", table_name="payment_proofs")
    op.drop_index("ix_payment_proofs_business_id", table_name="payment_proofs")
    op.drop_table("payment_proofs")

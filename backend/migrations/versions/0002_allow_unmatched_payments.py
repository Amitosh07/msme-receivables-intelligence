"""Allow unmatched payments and add invoice_reference

Revision ID: 0002_allow_unmatched_payments
Revises: 0001_initial_phase3_schema
Create Date: 2026-09-10 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0002_allow_unmatched_payments'
down_revision: Union[str, None] = '0001_initial_phase3_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Allow invoice_id to be nullable for payments imported prior to invoice ingestion
    op.alter_column(
        'payments',
        'invoice_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    # Add invoice_reference to store the raw invoice identifier from payment history CSV
    op.add_column(
        'payments',
        sa.Column('invoice_reference', sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f('ix_payments_invoice_reference'),
        'payments',
        ['invoice_reference'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_payments_invoice_reference'), table_name='payments')
    op.drop_column('payments', 'invoice_reference')
    op.alter_column(
        'payments',
        'invoice_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

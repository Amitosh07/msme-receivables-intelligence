"""Add note column to payments table for manual payment workflow."""
from alembic import op
import sqlalchemy as sa
revision = '0009'
# The preceding migration's revision ID is intentionally descriptive, not the
# numeric filename prefix.  Referencing the filename prefix broke Alembic's
# revision graph and prevented later Phase E/F migrations from running.
down_revision = '0008_backfill_payment_provenance'

def upgrade():
    op.add_column('payments', sa.Column('note', sa.String(512), nullable=True))

def downgrade():
    op.drop_column('payments', 'note')

"""Add risk_tier to prediction_results

Revision ID: 0003_add_risk_tier
Revises: 0002_allow_unmatched_payments
Create Date: 2026-09-10 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_add_risk_tier'
down_revision: Union[str, None] = '0002_allow_unmatched_payments'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'prediction_results',
        sa.Column('risk_tier', sa.String(length=16), nullable=True),
    )
    op.create_index(
        op.f('ix_prediction_results_risk_tier'),
        'prediction_results',
        ['risk_tier'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_prediction_results_risk_tier'), table_name='prediction_results')
    op.drop_column('prediction_results', 'risk_tier')

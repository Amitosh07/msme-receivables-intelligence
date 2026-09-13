"""Add explicit historical company context and normalized-name uniqueness.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "has_historical_context",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE customers AS c
        SET has_historical_context = true
        WHERE EXISTS (
            SELECT 1 FROM invoices AS i
            WHERE i.business_id = c.business_id
              AND i.customer_id = c.id
              AND i.origin = 'HISTORICAL'
        )
        """
    )
    op.drop_index("ix_customers_business_normalized_name", table_name="customers")
    op.create_index(
        "uq_customers_business_normalized_name",
        "customers",
        ["business_id", "normalized_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_customers_business_normalized_name", table_name="customers")
    op.create_index(
        "ix_customers_business_normalized_name",
        "customers",
        ["business_id", "normalized_name"],
    )
    op.drop_column("customers", "has_historical_context")

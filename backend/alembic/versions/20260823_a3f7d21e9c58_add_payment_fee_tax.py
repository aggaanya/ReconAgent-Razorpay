"""add payment fee/tax columns

Revision ID: a3f7d21e9c58
Revises: c61429b4e8f4
Create Date: 2026-08-23 16:20:11.302914

Razorpay levies ``fee``/``tax`` (minor-unit integers) on captured payments
only, so both columns are nullable BigInteger — no backfill is possible or
needed for uncaptured records.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a3f7d21e9c58'
down_revision: str | None = 'c61429b4e8f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'payments',
        sa.Column('fee_minor', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'payments',
        sa.Column('tax_minor', sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('payments', 'tax_minor')
    op.drop_column('payments', 'fee_minor')

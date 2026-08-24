"""drop sync_runs table

Revision ID: d8e2f41a7c03
Revises: b9e4c07f2d61
Create Date: 2026-08-24 00:00:00.000000

The external payment-provider sync workflow was removed; its run-metadata
table has no remaining writer or reader in the application layer. The
downgrade recreates the historical table verbatim (columns from
c61429b4e8f4 plus the six counter columns from b9e4c07f2d61) so the
migration chain stays fully reversible.

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd8e2f41a7c03'
down_revision: str | None = 'b9e4c07f2d61'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index('ix_sync_runs_started_at', table_name='sync_runs')
    op.drop_table('sync_runs')


def downgrade() -> None:
    op.create_table('sync_runs',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payments_fetched', sa.Integer(), nullable=False),
    sa.Column('payments_inserted', sa.Integer(), nullable=False),
    sa.Column('payments_updated', sa.Integer(), nullable=False),
    sa.Column('settlements_fetched', sa.Integer(), nullable=False),
    sa.Column('settlements_inserted', sa.Integer(), nullable=False),
    sa.Column('settlements_updated', sa.Integer(), nullable=False),
    *[sa.Column(c, sa.Integer(), nullable=False, server_default='0') for c in (
        'orders_fetched', 'orders_inserted', 'orders_updated',
        'refunds_fetched', 'refunds_inserted', 'refunds_updated',
    )],
    sa.Column('payments_watermark_epoch', sa.BigInteger(), nullable=True),
    sa.Column('settlements_watermark_epoch', sa.BigInteger(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('triggered_by', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('running', 'success', 'partial_failure', 'failed')", name='ck_sync_runs_status'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sync_runs_started_at', 'sync_runs', ['started_at'], unique=False)

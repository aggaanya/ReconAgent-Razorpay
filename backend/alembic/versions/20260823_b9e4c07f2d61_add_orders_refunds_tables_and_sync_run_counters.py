"""add orders/refunds tables and sync-run counters

Revision ID: b9e4c07f2d61
Revises: a3f7d21e9c58
Create Date: 2026-08-23 17:05:44.118220

Two concerns in one revision because they ship together:

1. ``orders`` / ``refunds`` tables — same money policy as ``payments``
   (minor-unit BigInteger, never float), provider-id UNIQUE constraints for
   idempotent re-syncs, and indexes only where scans are expected
   (provider_created_at ranges; refunds also by parent payment id).
2. Six per-resource counter columns on ``sync_runs`` so orders/refunds
   phases report fetched/inserted/updated like payments/settlements already
   do. Server default '0' keeps historical rows valid; skipped counts are
   derived at read time (fetched − inserted − updated) and not persisted.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b9e4c07f2d61'
down_revision: str | None = 'a3f7d21e9c58'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('orders',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('razorpay_order_id', sa.String(length=64), nullable=False),
    sa.Column('entity', sa.String(length=32), nullable=True),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('amount_paid_minor', sa.BigInteger(), nullable=False),
    sa.Column('amount_due_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('receipt', sa.String(length=255), nullable=True),
    sa.Column('notes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('provider_created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('razorpay_order_id', name='uq_orders_razorpay_order_id')
    )
    op.create_index('ix_orders_provider_created_at', 'orders', ['provider_created_at'], unique=False)
    op.create_table('refunds',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('razorpay_refund_id', sa.String(length=64), nullable=False),
    sa.Column('entity', sa.String(length=32), nullable=True),
    sa.Column('razorpay_payment_id', sa.String(length=64), nullable=False),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('speed', sa.String(length=16), nullable=True),
    sa.Column('notes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('provider_created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('razorpay_refund_id', name='uq_refunds_razorpay_refund_id')
    )
    op.create_index('ix_refunds_razorpay_payment_id', 'refunds', ['razorpay_payment_id'], unique=False)
    op.create_index('ix_refunds_provider_created_at', 'refunds', ['provider_created_at'], unique=False)

    for column in (
        'orders_fetched',
        'orders_inserted',
        'orders_updated',
        'refunds_fetched',
        'refunds_inserted',
        'refunds_updated',
    ):
        op.add_column(
            'sync_runs',
            sa.Column(column, sa.Integer(), nullable=False,
                      server_default='0'),
        )


def downgrade() -> None:
    for column in (
        'refunds_updated',
        'refunds_inserted',
        'refunds_fetched',
        'orders_updated',
        'orders_inserted',
        'orders_fetched',
    ):
        op.drop_column('sync_runs', column)

    op.drop_index('ix_refunds_provider_created_at', table_name='refunds')
    op.drop_index('ix_refunds_razorpay_payment_id', table_name='refunds')
    op.drop_table('refunds')
    op.drop_index('ix_orders_provider_created_at', table_name='orders')
    op.drop_table('orders')

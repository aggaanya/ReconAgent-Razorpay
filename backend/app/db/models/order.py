"""Order persistence model.

Money policy mirrors :mod:`app.db.models.payment`: all amounts are exact
minor-unit integers stored as ``BigInteger`` — never floating point.

Indexes (deliberate subset):

- UNIQUE on ``razorpay_order_id`` — provider-guaranteed uniqueness makes
  repeated fetches idempotent.
- ``provider_created_at`` — finance date-range scans. ``status`` stays
  unindexed (low cardinality, always paired with time bounds).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._timestamps import TimestampMixin
from .payment import RAZORPAY_ID_LENGTH, NotesJSON


class Order(TimestampMixin, Base):
    """One Razorpay order, persisted verbatim from the normalized schema."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("razorpay_order_id", name="uq_orders_razorpay_order_id"),
        Index("ix_orders_provider_created_at", "provider_created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    razorpay_order_id: Mapped[str] = mapped_column(
        String(RAZORPAY_ID_LENGTH), nullable=False
    )
    entity: Mapped[str | None] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_paid_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    amount_due_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    receipt: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[dict[str, Any]] = mapped_column(NotesJSON, default=dict)
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Order {self.razorpay_order_id} status={self.status!r}>"

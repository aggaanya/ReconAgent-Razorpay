"""Refund persistence model.

Money policy mirrors :mod:`app.db.models.payment`: ``amount_minor`` is an
exact minor-unit integer stored as ``BigInteger`` — never floating point.

Indexes (deliberate subset):

- UNIQUE on ``razorpay_refund_id`` — provider-guaranteed uniqueness makes
  repeated fetches idempotent.
- ``razorpay_payment_id`` — refunds-by-payment lookups for reconciliation
  and net-amount math. No hard FK: a refund can be synced before its parent
  payment, and provider ids are the join contract anyway.
- ``provider_created_at`` — finance date-range scans.
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

# Documented Refund.status values (spec §1.3/§4.1). Only "processed" money
# actually moved, so only processed refunds count toward realized Refund
# Amount / Refund Count; "pending" is a future outflow and "failed" moved
# nothing — both excluded from realized metrics.
REFUND_STATUS_PENDING = "pending"
REFUND_STATUS_PROCESSED = "processed"
REFUND_STATUS_FAILED = "failed"


class Refund(TimestampMixin, Base):
    """One Razorpay refund, persisted verbatim from the normalized schema."""

    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint(
            "razorpay_refund_id", name="uq_refunds_razorpay_refund_id"
        ),
        Index("ix_refunds_razorpay_payment_id", "razorpay_payment_id"),
        Index("ix_refunds_provider_created_at", "provider_created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    razorpay_refund_id: Mapped[str] = mapped_column(
        String(RAZORPAY_ID_LENGTH), nullable=False
    )
    entity: Mapped[str | None] = mapped_column(String(32))
    razorpay_payment_id: Mapped[str] = mapped_column(
        String(RAZORPAY_ID_LENGTH), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    speed: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[dict[str, Any]] = mapped_column(NotesJSON, default=dict)
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Refund {self.razorpay_refund_id} status={self.status!r}>"

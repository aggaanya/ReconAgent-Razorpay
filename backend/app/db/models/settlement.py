"""Settlement persistence model.

Money policy mirrors :mod:`app.db.models.payment`: ``amount_minor``,
``fees_minor`` and ``tax_minor`` are exact minor-unit integers stored as
``BigInteger`` — never floating point.

Indexes (deliberate subset):

- UNIQUE on ``razorpay_settlement_id`` — provider-guaranteed uniqueness;
  makes repeated fetches idempotent.
- ``utr`` — the bank Unique Transaction Reference is the natural join key
  against bank statement data during reconciliation.
- ``provider_created_at`` — incremental sync windows and settlement-date
  range scans. ``status`` stays unindexed (low cardinality, always paired
  with time bounds).
"""

from datetime import datetime

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
from .payment import RAZORPAY_ID_LENGTH


class Settlement(TimestampMixin, Base):
    """One Razorpay settlement, persisted verbatim from the normalized schema."""

    __tablename__ = "settlements"
    __table_args__ = (
        UniqueConstraint(
            "razorpay_settlement_id",
            name="uq_settlements_razorpay_settlement_id",
        ),
        Index("ix_settlements_provider_created_at", "provider_created_at"),
        Index("ix_settlements_utr", "utr"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    razorpay_settlement_id: Mapped[str] = mapped_column(
        String(RAZORPAY_ID_LENGTH), nullable=False
    )
    entity: Mapped[str | None] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fees_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    utr: Mapped[str | None] = mapped_column(String(RAZORPAY_ID_LENGTH))
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Settlement {self.razorpay_settlement_id} status={self.status!r}>"
        )

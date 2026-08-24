"""Payment persistence model.

Design rules:

- Money is stored as exact integers in the currency's smallest unit
  (``amount_minor``, paise for INR) using ``BigInteger`` — never floating
  point. Razorpay itself sends minor-unit integers, so this is lossless
  end to end.
- ``razorpay_payment_id`` carries a UNIQUE constraint: the provider
  guarantees payment id uniqueness, and the constraint is what makes
  repeated fetches idempotent at the storage layer.
- Indexes are deliberate, not exhaustive: reconciliation looks payments up
  by provider id (unique index), by ``order_id`` / ``invoice_id``
  (correlation with our own orders/invoices), and by ``provider_created_at``
  ranges (incremental sync windows). Low-cardinality columns such as
  ``status`` are left unindexed; they are only filtered combined with time
  ranges, where the ``provider_created_at`` index already bounds the scan.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._timestamps import TimestampMixin

# Portable JSON: JSONB on PostgreSQL (indexable, binary), JSON elsewhere.
NotesJSON = JSON().with_variant(JSONB(), "postgresql")

# Razorpay ids are ~18 chars ("pay_" + 14); 64 leaves generous headroom.
RAZORPAY_ID_LENGTH = 64

# Documented Payment.status enum (spec §1.1 / §2.0, razorpay.com/docs/api/
# payments/entity/). Classification per FINANCE_METRICS_SPECIFICATION.md
# §2.0: "captured" is successful; "refunded" also counts as successful
# (it is reached only after capture — spec §2.0 explicit rule and §3.6
# recommendation, confirmed by Part 13 case #6); "failed" is failed;
# "created"/"authorized" are in-progress and excluded from both buckets;
# anything else is unknown and must be anomaly-logged, never silently
# classified.
PAYMENT_STATUS_CREATED = "created"
PAYMENT_STATUS_AUTHORIZED = "authorized"
PAYMENT_STATUS_CAPTURED = "captured"
PAYMENT_STATUS_REFUNDED = "refunded"
PAYMENT_STATUS_FAILED = "failed"
PAYMENT_SUCCESS_STATUSES = frozenset(
    {PAYMENT_STATUS_CAPTURED, PAYMENT_STATUS_REFUNDED}
)
PAYMENT_FAILED_STATUSES = frozenset({PAYMENT_STATUS_FAILED})
PAYMENT_KNOWN_STATUSES = frozenset(
    {
        PAYMENT_STATUS_CREATED,
        PAYMENT_STATUS_AUTHORIZED,
        PAYMENT_STATUS_CAPTURED,
        PAYMENT_STATUS_REFUNDED,
        PAYMENT_STATUS_FAILED,
    }
)


class Payment(TimestampMixin, Base):
    """One Razorpay payment, persisted verbatim from the normalized schema."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "razorpay_payment_id",
            name="uq_payments_razorpay_payment_id",
        ),
        Index("ix_payments_provider_created_at", "provider_created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    razorpay_payment_id: Mapped[str] = mapped_column(
        String(RAZORPAY_ID_LENGTH), nullable=False
    )
    entity: Mapped[str | None] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    order_id: Mapped[str | None] = mapped_column(
        String(RAZORPAY_ID_LENGTH), index=True
    )
    invoice_id: Mapped[str | None] = mapped_column(
        String(RAZORPAY_ID_LENGTH), index=True
    )
    method: Mapped[str | None] = mapped_column(String(32))
    captured: Mapped[bool | None] = mapped_column(Boolean)
    # Platform fee/tax in minor units; nullable because Razorpay only
    # levies them on captured payments.
    fee_minor: Mapped[int | None] = mapped_column(BigInteger)
    tax_minor: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[dict[str, Any]] = mapped_column(NotesJSON, default=dict)
    # Provider-side creation time (Razorpay epoch seconds → UTC). Distinct
    # from created_at/updated_at, which describe this row's lifecycle.
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Payment {self.razorpay_payment_id} status={self.status!r}>"

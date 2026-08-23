"""Synchronization run metadata.

One row per execution of the Razorpay sync workflow. Justified as its own
table because the data must outlive the process:

- incremental fetching needs the last successful per-resource watermark
  (max provider ``created_at`` epoch seen) to compute ``from`` filters;
- operators need an audit trail: when did a run start/finish, how many
  records were fetched vs inserted vs updated, and what failed.

Counters are explicit columns (queryable) rather than a JSON blob.
``error`` stores only sanitized exception messages — never credentials,
URLs, or raw provider payloads.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._timestamps import TimestampMixin


class SyncStatus(str, enum.Enum):
    """Lifecycle of one synchronization run."""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class SyncRun(TimestampMixin, Base):
    """Metadata and outcome of one Razorpay synchronization run."""

    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'partial_failure', 'failed')",
            name="ck_sync_runs_status",
        ),
        Index("ix_sync_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SyncStatus.RUNNING.value
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Per-resource counters (records fetched from the API / inserted /
    # updated in our store). Orders/refunds have no watermark: Razorpay's
    # list endpoints for them expose no date filters, so those phases
    # restart at skip=0 each run and rely on idempotent upserts.
    payments_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payments_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payments_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refunds_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refunds_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refunds_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settlements_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settlements_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settlements_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Incremental cursors: max provider created_at epoch successfully
    # persisted per resource. None until that phase first succeeds.
    payments_watermark_epoch: Mapped[int | None] = mapped_column(BigInteger)
    settlements_watermark_epoch: Mapped[int | None] = mapped_column(BigInteger)

    # Sanitized failure description (see module docstring).
    error: Mapped[str | None] = mapped_column(Text)

    triggered_by: Mapped[str] = mapped_column(String(32), nullable=False, default="api")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SyncRun {self.id} status={self.status!r}>"

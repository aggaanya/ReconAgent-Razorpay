"""Synchronization workflow schemas (API surface only — no logic)."""

from datetime import datetime

from pydantic import BaseModel, Field


class SyncResourceStats(BaseModel):
    """Counters for one resource within a synchronization run."""

    fetched: int = Field(ge=0, description="Records fetched from Razorpay")
    inserted: int = Field(ge=0, description="New rows created")
    updated: int = Field(ge=0, description="Existing rows changed")
    skipped: int = Field(
        ge=0,
        description="Existing rows already identical (no write needed)",
    )


class SyncRunResponse(BaseModel):
    """Status/metadata of one synchronization run."""

    run_id: int
    status: str = Field(
        description="running | success | partial_failure | failed"
    )
    error: str | None = Field(
        default=None, description="Sanitized failure message, if any"
    )
    payments: SyncResourceStats
    refunds: SyncResourceStats
    orders: SyncResourceStats
    settlements: SyncResourceStats
    payments_watermark_epoch: int | None = None
    settlements_watermark_epoch: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

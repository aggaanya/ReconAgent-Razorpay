"""Synchronization API — thin trigger/status surface only.

All workflow logic lives in :mod:`app.services.sync`; routes here build
dependencies, call the service, and translate outcomes to HTTP. No fetch,
persistence, or retry decisions are made in this module.

- ``POST /api/v1/sync/runs``       — run one synchronization synchronously.
- ``GET  /api/v1/sync/runs/{id}``  — inspect a recorded run.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as OrmSession

from app.core.config import get_settings
from app.db.models import SyncRun, SyncStatus
from app.db.session import DatabaseNotConfiguredError, get_db, get_session_factory
from app.integrations.razorpay import RazorpayError, RazorpayService
from app.schemas.sync import SyncResourceStats, SyncRunResponse
from app.services.sync import RazorpaySyncService, SyncOutcome

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])

_service: RazorpaySyncService | None = None


def build_sync_service() -> RazorpaySyncService:
    """Construct the sync service from current settings (no caching)."""
    settings = get_settings()
    fetcher = RazorpayService.from_settings(settings)
    return RazorpaySyncService(
        fetcher=fetcher,
        session_factory=get_session_factory(),
        page_size=settings.razorpay_sync_page_size,
        max_pages=settings.razorpay_sync_max_pages,
        page_delay_seconds=settings.razorpay_sync_page_delay_seconds,
    )


def get_sync_service() -> RazorpaySyncService:
    """Dependency: cached service; rebuilt after resets (tests/lifespan)."""
    global _service
    if _service is None:
        try:
            _service = build_sync_service()
        except (RazorpayError, DatabaseNotConfiguredError) as exc:
            # Missing credentials or DATABASE_URL: clear 503, no internals.
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _service


def reset_sync_service() -> None:
    """Close and discard the cached service (lifespan shutdown / tests)."""
    global _service
    if _service is not None:
        _service.close()
        _service = None


def _response_from_outcome(outcome: SyncOutcome) -> SyncRunResponse:
    def stats(resource: Any) -> SyncResourceStats:
        return SyncResourceStats(
            fetched=resource.fetched,
            inserted=resource.inserted,
            updated=resource.updated,
            skipped=resource.skipped,
        )

    return SyncRunResponse(
        run_id=outcome.run_id,
        status=outcome.status,
        error=outcome.error,
        payments=stats(outcome.payments),
        refunds=stats(outcome.refunds),
        orders=stats(outcome.orders),
        settlements=stats(outcome.settlements),
    )


def _response_from_run(run: SyncRun) -> SyncRunResponse:
    def stats(prefix: str) -> SyncResourceStats:
        fetched = getattr(run, f"{prefix}_fetched")
        inserted = getattr(run, f"{prefix}_inserted")
        updated = getattr(run, f"{prefix}_updated")
        # skipped is not persisted; derive it from the stored counters.
        return SyncResourceStats(
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            skipped=max(fetched - inserted - updated, 0),
        )

    return SyncRunResponse(
        run_id=run.id,
        status=run.status,
        error=run.error,
        payments=stats("payments"),
        refunds=stats("refunds"),
        orders=stats("orders"),
        settlements=stats("settlements"),
        payments_watermark_epoch=run.payments_watermark_epoch,
        settlements_watermark_epoch=run.settlements_watermark_epoch,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.post("/runs", response_model=SyncRunResponse, summary="Run a synchronization")
def trigger_sync(
    service: RazorpaySyncService = Depends(get_sync_service),
) -> SyncRunResponse | JSONResponse:
    """Execute one bounded synchronization run
    (payments -> refunds -> orders -> settlements).

    Outcomes map to: ``success``/``partial_failure`` -> 200 (partial means
    earlier phases persisted and the failure is recorded), ``failed`` ->
    502.
    """
    try:
        outcome = service.run_sync(triggered_by="api")
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    body = _response_from_outcome(outcome)
    if outcome.status == SyncStatus.FAILED.value:
        return JSONResponse(status_code=502, content=body.model_dump(mode="json"))
    return body


@router.get("/runs/{run_id}", response_model=SyncRunResponse)
def get_sync_run(
    run_id: int,
    session: OrmSession = Depends(get_db),
) -> SyncRunResponse:
    try:
        run = session.get(SyncRun, run_id)
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail=f"Sync run {run_id} not found")
    return _response_from_run(run)


__all__ = ["get_sync_service", "reset_sync_service", "router"]

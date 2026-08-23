"""Data access for :class:`app.db.models.SyncRun`."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SyncRun, SyncStatus
from .base import paginate

MAX_PAGE_LIMIT = 100


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SyncRunRepository:
    """Create/finalize synchronization runs and read incremental cursors."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- write path ------------------------------------------------------

    def create(self, *, triggered_by: str = "api") -> SyncRun:
        run = SyncRun(status=SyncStatus.RUNNING.value, triggered_by=triggered_by)
        self._session.add(run)
        self._session.flush()
        return run

    def mark_success(self, run: SyncRun) -> SyncRun:
        run.status = SyncStatus.SUCCESS.value
        run.completed_at = utc_now()
        run.error = None
        self._session.flush()
        return run

    def mark_partial_failure(self, run: SyncRun, error: str) -> SyncRun:
        """One phase succeeded and is committed; the other failed."""
        run.status = SyncStatus.PARTIAL_FAILURE.value
        run.completed_at = utc_now()
        run.error = error
        self._session.flush()
        return run

    def mark_failed(self, run: SyncRun, error: str) -> SyncRun:
        run.status = SyncStatus.FAILED.value
        run.completed_at = utc_now()
        run.error = error
        self._session.flush()
        return run

    # --- cursors ---------------------------------------------------------

    def latest_watermark(
        self, column_name: str
    ) -> int | None:
        """Watermark from the most recent successful run (``None`` if never)."""
        column = getattr(SyncRun, column_name)
        statement = (
            select(column)
            .where(SyncRun.status == SyncStatus.SUCCESS.value)
            .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    # --- read path -------------------------------------------------------

    def get(self, run_id: int) -> SyncRun | None:
        return self._session.get(SyncRun, run_id)

    def list_paginated(
        self, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[SyncRun], int]:
        """Most recent runs first plus total count."""
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                select(SyncRun)
                .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
                .limit(limit)
                .offset(offset)
            ).scalars(),
            count_statement_factory=lambda: select(func.count()).select_from(
                SyncRun
            ),
            limit=limit,
            offset=offset,
        )

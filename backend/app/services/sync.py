"""Controlled Razorpay synchronization workflow.

Pipeline:

    Razorpay API -> client (bounded retries) -> DTO -> mapper
        -> normalized domain schema -> repository upsert -> PostgreSQL

Resources and phase order (a failure stops later phases):

    payments -> refunds -> orders -> settlements

Guarantees:

- **Idempotent.** Records are keyed on provider-unique ids and written via
  change-detecting repository upserts; re-running over the same data skips
  identical rows and updates changed ones instead of duplicating them.
- **Incremental where the provider allows it.** Payments and settlements
  keep a watermark: the max provider ``created_at`` epoch successfully
  persisted. The next run passes it as the API's inclusive ``from`` filter,
  so only new/changed records are fetched. A boundary record can be
  re-fetched (inclusive window) — harmless, because writes are upserts.
  Razorpay documents no date filters for orders/refunds list endpoints, so
  those phases restart at ``skip=0`` every run, bounded by ``max_pages``,
  and rely on idempotent upserts instead of a cursor.
- **Partially-failure-safe.** Commits happen per page. If a late phase
  fails after an earlier one succeeded, earlier pages stay committed (never
  rolled back), the failing page's writes are discarded, and the run is
  recorded as ``partial_failure`` with a sanitized error message. A failure
  in the first phase records plain ``failed``.
- **Rate-limit friendly.** All reads go through the existing client, which
  retries transient 429/5xx responses with bounded backoff honoring
  ``Retry-After``; this service additionally paces pages with a small delay
  and bounds every run to ``max_pages`` pages per resource.
- **Secret-free observability.** Logs and stored metadata contain counts,
  statuses, and sanitized messages only — never credentials or URLs.

Synchronization logic deliberately lives here; ``app/api/sync.py`` is a
thin trigger/status surface that delegates everything to this service.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.db.mappers import (
    order_from_normalized,
    payment_from_normalized,
    refund_from_normalized,
    settlement_from_normalized,
)
from app.db.models import SyncStatus
from app.db.repositories import (
    OrderRepository,
    PaymentRepository,
    RefundRepository,
    SettlementRepository,
    SyncRunRepository,
)
from app.integrations.razorpay.exceptions import RazorpayError

logger = logging.getLogger(__name__)

Sleep = Callable[[float], None]


@dataclass
class ResourceStats:
    """Per-resource counters for one synchronization run."""

    name: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    page_count: int = 0

    def next_skip(self, page_size: int) -> int:
        """Provider-side offset of the next page."""
        return self.page_count * page_size

    def record_page(
        self,
        *,
        fetched: int,
        inserted: int,
        updated: int,
        skipped: int = 0,
    ) -> None:
        self.fetched += fetched
        self.inserted += inserted
        self.updated += updated
        self.skipped += skipped
        self.page_count += 1


@dataclass
class SyncOutcome:
    """Result of one synchronization run (mirrors its SyncRun row)."""

    run_id: int
    status: str
    payments: ResourceStats = field(default_factory=lambda: ResourceStats("payments"))
    refunds: ResourceStats = field(default_factory=lambda: ResourceStats("refunds"))
    orders: ResourceStats = field(default_factory=lambda: ResourceStats("orders"))
    settlements: ResourceStats = field(
        default_factory=lambda: ResourceStats("settlements")
    )
    error: str | None = None


class SyncFetcher(Protocol):
    """Structural contract satisfied by ``RazorpayService`` (and fakes)."""

    def list_payments(
        self,
        *,
        count: int,
        skip: int,
        from_epoch: int | None = None,
        to_epoch: int | None = None,
    ) -> Any: ...

    def list_refunds(self, *, count: int, skip: int) -> Any: ...

    def list_orders(self, *, count: int, skip: int) -> Any: ...

    def list_settlements(
        self,
        *,
        count: int,
        skip: int,
        from_epoch: int | None = None,
        to_epoch: int | None = None,
    ) -> Any: ...


def _epoch(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return int(dt.timestamp())


class RazorpaySyncService:
    """Orchestrates fetch -> normalize -> persist for all four resources."""

    def __init__(
        self,
        *,
        fetcher: SyncFetcher,
        session_factory: sessionmaker[Session],
        page_size: int = 100,
        max_pages: int = 200,
        page_delay_seconds: float = 0.25,
        sleep: Sleep = time.sleep,
    ) -> None:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if page_delay_seconds < 0:
            raise ValueError("page_delay_seconds must be non-negative")
        self._fetcher = fetcher
        self._session_factory = session_factory
        self._page_size = page_size
        self._max_pages = max_pages
        self._page_delay_seconds = page_delay_seconds
        self._sleep = sleep

    def close(self) -> None:
        """Release fetcher resources (no-op for fakes without close)."""
        close = getattr(self._fetcher, "close", None)
        if callable(close):
            close()

    # --- public entrypoint -------------------------------------------------

    def run_sync(self, *, triggered_by: str = "api") -> SyncOutcome:
        """Execute one full synchronization run.

        Never raises integration failures: they are captured in the run
        record and reported through the returned outcome.
        """
        session = self._session_factory()
        try:
            runs = SyncRunRepository(session)
            run = runs.create(triggered_by=triggered_by)
            session.commit()
            logger.info("Sync run %d started", run.id)

            outcome = SyncOutcome(run_id=run.id, status=SyncStatus.FAILED.value)

            phases: tuple[tuple[ResourceStats, dict[str, Any]], ...] = (
                (
                    outcome.payments,
                    dict(
                        repo_factory=PaymentRepository,
                        mapper=payment_from_normalized,
                        fetch_page=self._fetcher.list_payments,
                        watermark_column="payments_watermark_epoch",
                        watermark_reader=lambda r: r.latest_watermark(
                            "payments_watermark_epoch"
                        ),
                    ),
                ),
                (
                    outcome.refunds,
                    dict(
                        repo_factory=RefundRepository,
                        mapper=refund_from_normalized,
                        fetch_page=self._fetcher.list_refunds,
                        watermark_column=None,
                        watermark_reader=None,
                    ),
                ),
                (
                    outcome.orders,
                    dict(
                        repo_factory=OrderRepository,
                        mapper=order_from_normalized,
                        fetch_page=self._fetcher.list_orders,
                        watermark_column=None,
                        watermark_reader=None,
                    ),
                ),
                (
                    outcome.settlements,
                    dict(
                        repo_factory=SettlementRepository,
                        mapper=settlement_from_normalized,
                        fetch_page=self._fetcher.list_settlements,
                        watermark_column="settlements_watermark_epoch",
                        watermark_reader=lambda r: r.latest_watermark(
                            "settlements_watermark_epoch"
                        ),
                    ),
                ),
            )

            for index, (stats, options) in enumerate(phases):
                error = self._sync_resource(session, run, stats=stats, **options)
                if error is not None:
                    if index == 0:
                        # First phase: nothing persisted yet -> plain failure.
                        runs.mark_failed(run, error)
                        outcome.status = SyncStatus.FAILED.value
                    else:
                        # Earlier phases are already committed; nothing is
                        # corrupted.
                        runs.mark_partial_failure(run, error)
                        outcome.status = SyncStatus.PARTIAL_FAILURE.value
                    session.commit()
                    logger.warning(
                        "Sync run %d %s during %s phase: %s",
                        run.id,
                        outcome.status,
                        stats.name,
                        error,
                    )
                    outcome.error = error
                    return outcome
                logger.info(
                    "Sync run %d %s phase done (fetched=%d inserted=%d "
                    "updated=%d skipped=%d pages=%d)",
                    run.id,
                    stats.name,
                    stats.fetched,
                    stats.inserted,
                    stats.updated,
                    stats.skipped,
                    stats.page_count,
                )

            runs.mark_success(run)
            session.commit()
            outcome.status = SyncStatus.SUCCESS.value
            logger.info(
                "Sync run %d success (payments fetched=%d; refunds "
                "fetched=%d; orders fetched=%d; settlements fetched=%d)",
                run.id,
                outcome.payments.fetched,
                outcome.refunds.fetched,
                outcome.orders.fetched,
                outcome.settlements.fetched,
            )
            return outcome
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # --- internals ---------------------------------------------------------

    def _sync_resource(
        self,
        session: Session,
        run: Any,
        *,
        stats: ResourceStats,
        repo_factory: Callable[[Session], Any],
        mapper: Callable[[Any], Any],
        fetch_page: Callable[..., Any],
        watermark_column: str | None,
        watermark_reader: Callable[[SyncRunRepository], int | None] | None,
    ) -> str | None:
        """Fetch + persist one resource page by page.

        Returns ``None`` on success or a sanitized error string on failure.
        Commits per page so earlier pages survive later failures. Resources
        with ``watermark_column=None`` (orders/refunds — the provider offers
        no date filters for them) always restart at ``skip=0``; idempotent
        upserts keep repeated fetches harmless.
        """
        from_epoch = (
            watermark_reader(SyncRunRepository(session))
            if watermark_reader is not None
            else None
        )
        repo = repo_factory(session)

        while True:
            try:
                if watermark_column is not None:
                    page = fetch_page(
                        count=self._page_size,
                        skip=stats.next_skip(self._page_size),
                        from_epoch=from_epoch,
                    )
                else:
                    page = fetch_page(
                        count=self._page_size,
                        skip=stats.next_skip(self._page_size),
                    )
                items = list(page.items)
            except RazorpayError as exc:
                # Discard this page's uncommitted writes; keep prior pages.
                session.rollback()
                return str(exc)

            inserted = updated = skipped = 0
            max_epoch_seen: int | None = None
            for item in items:
                _, outcome_state = repo.upsert_detailed(mapper(item))
                if outcome_state == "inserted":
                    inserted += 1
                elif outcome_state == "updated":
                    updated += 1
                else:
                    skipped += 1
                created = _epoch(getattr(item, "created_at", None))
                if created is not None and (
                    max_epoch_seen is None or created > max_epoch_seen
                ):
                    max_epoch_seen = created

            stats.record_page(
                fetched=len(items),
                inserted=inserted,
                updated=updated,
                skipped=skipped,
            )

            # Track run counters alongside the data so every commit leaves
            # them mutually consistent. Column names follow
            # "<resource>_<counter>" for all four resources.
            for counter, value in (
                ("fetched", len(items)),
                ("inserted", inserted),
                ("updated", updated),
            ):
                column = f"{stats.name}_{counter}"
                setattr(run, column, getattr(run, column) + value)
            if watermark_column is not None and max_epoch_seen is not None:
                current: int | None = getattr(run, watermark_column)
                setattr(
                    run,
                    watermark_column,
                    max_epoch_seen
                    if current is None
                    else max(current, max_epoch_seen),
                )
            session.commit()

            if len(items) < self._page_size:
                break
            if stats.page_count >= self._max_pages:
                logger.warning(
                    "Sync run %d hit the %d-page cap for %s; stopping this "
                    "resource early%s",
                    run.id,
                    self._max_pages,
                    stats.name,
                    " (it will resume from the watermark)"
                    if watermark_column is not None
                    else "",
                )
                break
            if self._page_delay_seconds:
                self._sleep(self._page_delay_seconds)
        return None

"""Finance API — DB-backed reads over synced Razorpay records.

Conventions:

- Prefix ``/api/v1/finance``; everything here reads local persistence only
  (no provider calls — trigger ``POST /api/v1/sync/runs`` to refresh).
- ``start``/``end`` are ISO calendar dates interpreted as inclusive UTC day
  bounds; bad input yields a clean 422 from FastAPI.
- Metric-grade routes (``/payment-performance``, ``/revenue``, ``/trends``)
  take **named reporting periods** (``today``, ``yesterday``, ``this_week``,
  ``previous_week``, ``this_month``, ``previous_month``) resolved in IST per
  docs/FINANCE_METRICS_SPECIFICATION.md §6.1/§6.2 — never the server
  timezone. Every response carries its explicit window + timezone.
- Missing DATABASE_URL maps to a clear 503, consistent with the other
  database-backed routes. No credentials or internals leak in errors.
"""

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as OrmSession

from app.core.periods import (
    PERIOD_PRESETS,
    REPORTING_TIMEZONE,
    REPORTING_TIMEZONE_NAME,
    TREND_GRANULARITIES,
    named_period,
)
from app.db.session import DatabaseNotConfiguredError, get_db
from app.schemas.finance import (
    FinanceOrdersPage,
    FinancePaymentsPage,
    FinanceRefundsPage,
    FinanceSettlementsPage,
    FinanceSummaryResponse,
    PaymentPerformanceResponse,
    RevenueMetricsResponse,
    TrendResponse,
)
from app.services.metrics import (
    MAX_FINANCE_PAGE_LIMIT,
    TREND_METRIC_EXTRACTORS,
    FinanceService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

DEFAULT_METRIC_CURRENCY = "INR"


def _db_guard(exc: DatabaseNotConfiguredError) -> HTTPException:
    """Uniform 503 for unconfigured databases (no internals leaked)."""
    return HTTPException(status_code=503, detail=str(exc))


def _reporting_now() -> datetime:
    """Current instant in the reporting timezone (spec §6.2: never server tz)."""
    return datetime.now(tz=REPORTING_TIMEZONE)


def _resolve_period(period: str) -> tuple[datetime, datetime]:
    """Named preset -> half-open ``(start, end)`` in the reporting tz."""
    if period not in PERIOD_PRESETS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'period' must be one of {', '.join(PERIOD_PRESETS)} "
                f"(got {period!r})"
            ),
        )
    return named_period(period, _reporting_now())


def _validate_currency(currency: str) -> str:
    """Single-currency guard for metric aggregates (spec Part 9)."""
    normalized = currency.strip().upper()
    if not 2 < len(normalized) <= 8:
        raise HTTPException(
            status_code=422, detail="'currency' must be an ISO code"
        )
    return normalized


@router.get(
    "/summary",
    response_model=FinanceSummaryResponse,
    summary="Aggregated finance metrics per currency",
    description=(
        "Computes gross/successful/failed/refunded/net/settlement amounts "
        "(exact minor-unit integers) grouped by currency over the filtered "
        "window. Dates are inclusive UTC calendar days; `status`/`method` "
        "filter payments, `currency` applies to all resources."
    ),
)
def finance_summary(
    start_date: date | None = Query(
        None, alias="start", description="Inclusive window start (YYYY-MM-DD)"
    ),
    end_date: date | None = Query(
        None, alias="end", description="Inclusive window end (YYYY-MM-DD)"
    ),
    currency: str | None = Query(None, max_length=8),
    status: str | None = Query(None, max_length=32),
    method: str | None = Query(None, max_length=32),
    session: OrmSession = Depends(get_db),
) -> FinanceSummaryResponse:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422, detail="'start' must be less than or equal to 'end'"
        )
    try:
        return FinanceService.summary(
            session,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            status=status,
            method=method,
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


@router.get(
    "/payments",
    response_model=FinancePaymentsPage,
    summary="List stored payments with finance filters",
    description=(
        "Returns one bounded page of stored payments (exact minor-unit "
        "amounts) filtered by an inclusive UTC date window, currency, "
        "payment status, and method."
    ),
)
def finance_payments(
    start_date: date | None = Query(
        None, alias="start", description="Inclusive window start (YYYY-MM-DD)"
    ),
    end_date: date | None = Query(
        None, alias="end", description="Inclusive window end (YYYY-MM-DD)"
    ),
    currency: str | None = Query(None, max_length=8),
    status: str | None = Query(None, max_length=32),
    method: str | None = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=MAX_FINANCE_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    session: OrmSession = Depends(get_db),
) -> FinancePaymentsPage:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422, detail="'start' must be less than or equal to 'end'"
        )
    try:
        return FinanceService.list_payments(
            session,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            status=status,
            method=method,
            limit=limit,
            offset=offset,
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


@router.get(
    "/refunds",
    response_model=FinanceRefundsPage,
    summary="List stored refunds with finance filters",
    description=(
        "Returns one bounded page of stored refunds filtered by an "
        "inclusive UTC date window, currency, refund status, or parent "
        "payment id."
    ),
)
def finance_refunds(
    start_date: date | None = Query(
        None, alias="start", description="Inclusive window start (YYYY-MM-DD)"
    ),
    end_date: date | None = Query(
        None, alias="end", description="Inclusive window end (YYYY-MM-DD)"
    ),
    currency: str | None = Query(None, max_length=8),
    status: str | None = Query(None, max_length=32),
    payment_id: str | None = Query(
        None, max_length=64, description="Filter by parent payment id"
    ),
    limit: int = Query(50, ge=1, le=MAX_FINANCE_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    session: OrmSession = Depends(get_db),
) -> FinanceRefundsPage:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422, detail="'start' must be less than or equal to 'end'"
        )
    try:
        return FinanceService.list_refunds(
            session,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            status=status,
            payment_id=payment_id,
            limit=limit,
            offset=offset,
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


@router.get(
    "/settlements",
    response_model=FinanceSettlementsPage,
    summary="List stored settlements with finance filters",
    description=(
        "Returns one bounded page of stored settlements (amount/fees/tax "
        "in exact minor units) filtered by an inclusive UTC date window, "
        "currency, and settlement status."
    ),
)
def finance_settlements(
    start_date: date | None = Query(
        None, alias="start", description="Inclusive window start (YYYY-MM-DD)"
    ),
    end_date: date | None = Query(
        None, alias="end", description="Inclusive window end (YYYY-MM-DD)"
    ),
    currency: str | None = Query(None, max_length=8),
    status: str | None = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=MAX_FINANCE_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    session: OrmSession = Depends(get_db),
) -> FinanceSettlementsPage:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422, detail="'start' must be less than or equal to 'end'"
        )
    try:
        return FinanceService.list_settlements(
            session,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            status=status,
            limit=limit,
            offset=offset,
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


@router.get(
    "/orders",
    response_model=FinanceOrdersPage,
    summary="List stored orders with finance filters",
    description=(
        "Returns one bounded page of stored orders filtered by an "
        "inclusive UTC date window, currency, order status, and receipt."
    ),
)
def finance_orders(
    start_date: date | None = Query(
        None, alias="start", description="Inclusive window start (YYYY-MM-DD)"
    ),
    end_date: date | None = Query(
        None, alias="end", description="Inclusive window end (YYYY-MM-DD)"
    ),
    currency: str | None = Query(None, max_length=8),
    status: str | None = Query(None, max_length=32),
    receipt: str | None = Query(None, max_length=255),
    limit: int = Query(50, ge=1, le=MAX_FINANCE_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    session: OrmSession = Depends(get_db),
) -> FinanceOrdersPage:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422, detail="'start' must be less than or equal to 'end'"
        )
    try:
        return FinanceService.list_orders(
            session,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            status=status,
            receipt=receipt,
            limit=limit,
            offset=offset,
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


@router.get(
    "/payment-performance",
    response_model=PaymentPerformanceResponse,
    summary="Deterministic payment performance metrics for a named period",
    description=(
        "Transaction volume, successful/failed/in-progress counts and "
        "success/failure rates over a named reporting period resolved in "
        "IST (specification Part 2). Rates use transaction volume as "
        "denominator and are null when the period is empty. No LLM, no "
        "estimates — pure aggregation over stored payments."
    ),
)
def finance_payment_performance(
    period: str = Query(
        "today",
        description=(
            f"Named reporting period: {', '.join(PERIOD_PRESETS)} "
            "(IST calendar semantics)"
        ),
    ),
    currency: str = Query(
        DEFAULT_METRIC_CURRENCY,
        max_length=8,
        description="Single-currency aggregation filter (never mixed)",
    ),
    session: OrmSession = Depends(get_db),
) -> PaymentPerformanceResponse:
    start, end = _resolve_period(period)
    try:
        return FinanceService.payment_performance(
            session,
            start=start,
            end=end,
            currency=_validate_currency(currency),
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


@router.get(
    "/revenue",
    response_model=RevenueMetricsResponse,
    summary="Deterministic revenue metrics for a named period",
    description=(
        "Gross revenue (captured payments incl. refunded-status ones), "
        "processed-only refund amount/count/rate, Razorpay fee/tax sums, "
        "and the three separately-labeled net-revenue variants over a "
        "named reporting period resolved in IST (specification Parts 3-4)."
    ),
)
def finance_revenue(
    period: str = Query(
        "today",
        description=(
            f"Named reporting period: {', '.join(PERIOD_PRESETS)} "
            "(IST calendar semantics)"
        ),
    ),
    currency: str = Query(
        DEFAULT_METRIC_CURRENCY,
        max_length=8,
        description="Single-currency aggregation filter (never mixed)",
    ),
    session: OrmSession = Depends(get_db),
) -> RevenueMetricsResponse:
    start, end = _resolve_period(period)
    try:
        return FinanceService.revenue(
            session,
            start=start,
            end=end,
            currency=_validate_currency(currency),
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


@router.get(
    "/trends/{metric}",
    response_model=TrendResponse,
    summary="Period-over-period trend for one finance metric",
    description=(
        f"Compares a supported metric ({', '.join(TREND_METRIC_EXTRACTORS)}) "
        "across current vs previous day/week/month windows resolved in IST. "
        "Returns both values, absolute change, percentage change, and an "
        "explicit changeType; percentage change is null with "
        "'new_activity'/'no_activity' when the previous value is zero "
        "(specification Part 6.4)."
    ),
)
def finance_trend(
    metric: str,
    granularity: str = Query(
        "day", description=f"Comparison granularity: {', '.join(TREND_GRANULARITIES)}"
    ),
    currency: str = Query(
        DEFAULT_METRIC_CURRENCY,
        max_length=8,
        description="Single-currency aggregation filter (never mixed)",
    ),
    session: OrmSession = Depends(get_db),
) -> TrendResponse:
    if metric not in TREND_METRIC_EXTRACTORS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown metric {metric!r}; supported: "
                f"{', '.join(sorted(TREND_METRIC_EXTRACTORS))}"
            ),
        )
    if granularity not in TREND_GRANULARITIES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'granularity' must be one of "
                f"{', '.join(TREND_GRANULARITIES)} (got {granularity!r})"
            ),
        )
    try:
        return FinanceService.trend(
            session,
            metric=metric,
            granularity=granularity,
            now=_reporting_now(),
            currency=_validate_currency(currency),
        )
    except DatabaseNotConfiguredError as exc:
        raise _db_guard(exc) from exc


__all__ = ["router"]

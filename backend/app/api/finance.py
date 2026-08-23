"""Finance API — DB-backed reads over synced Razorpay records.

Conventions:

- Prefix ``/api/v1/finance``; everything here reads local persistence only
  (no provider calls — trigger ``POST /api/v1/sync/runs`` to refresh).
- ``start``/``end`` are ISO calendar dates interpreted as inclusive UTC day
  bounds; bad input yields a clean 422 from FastAPI.
- Missing DATABASE_URL maps to a clear 503, consistent with the other
  database-backed routes. No credentials or internals leak in errors.
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as OrmSession

from app.db.session import DatabaseNotConfiguredError, get_db
from app.schemas.finance import (
    FinanceOrdersPage,
    FinancePaymentsPage,
    FinanceRefundsPage,
    FinanceSettlementsPage,
    FinanceSummaryResponse,
)
from app.services.metrics import MAX_FINANCE_PAGE_LIMIT, FinanceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


def _db_guard(exc: DatabaseNotConfiguredError) -> HTTPException:
    """Uniform 503 for unconfigured databases (no internals leaked)."""
    return HTTPException(status_code=503, detail=str(exc))


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


__all__ = ["router"]

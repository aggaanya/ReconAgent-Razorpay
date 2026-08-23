"""Finance metrics and read models over synced Razorpay records.

Pure read layer: no fetching, no writes, no provider calls. All queries run
through the repositories; this service only composes them, applies date
semantics, and shapes results.

Date semantics: ``start``/``end`` are ISO calendar dates interpreted as UTC
day bounds (inclusive on both ends — ``end`` covers its full day). Filters:

- ``currency`` — exact match, applied to payments/refunds/settlements/orders.
- ``status`` / ``method`` — payment-specific attributes; applied to payments
  (and settlement status where noted), never fabricated for resources that
  lack them.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.repositories import (
    OrderRepository,
    PaymentRepository,
    RefundRepository,
    SettlementRepository,
)
from app.schemas.finance import (
    FinanceCurrencySummary,
    FinanceOrderRecord,
    FinanceOrdersPage,
    FinancePaymentRecord,
    FinancePaymentsPage,
    FinanceRefundRecord,
    FinanceRefundsPage,
    FinanceSettlementRecord,
    FinanceSettlementsPage,
    FinanceSummaryResponse,
)

MAX_FINANCE_PAGE_LIMIT = 500


def _utc_day_bounds(
    start: date | None, end: date | None
) -> tuple[datetime | None, datetime | None]:
    """Inclusive UTC datetimes covering whole calendar days.

    ``end`` maps to the last microsecond of that day so a single-day query
    returns every record stamped within it.
    """
    start_dt = (
        datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
        if start is not None
        else None
    )
    end_dt = None
    if end is not None:
        end_of_day = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
        end_dt = end_of_day + timedelta(days=1) - timedelta(microseconds=1)
    return start_dt, end_dt


class FinanceService:
    """Read-only finance queries over locally persisted records."""

    # --- summary ----------------------------------------------------------

    @staticmethod
    def summary(
        session: Session,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
        status: str | None = None,
        method: str | None = None,
    ) -> FinanceSummaryResponse:
        """Aggregate payments/refunds/settlements per currency.

        The union of currencies seen in any of the three resources forms the
        result groups; absent sides contribute zeros so ``net`` stays
        well-defined.
        """
        start, end = _utc_day_bounds(start_date, end_date)

        payment_aggregates = PaymentRepository(session).aggregate_by_currency(
            start=start, end=end, currency=currency, status=status, method=method
        )
        refund_aggregates = RefundRepository(session).aggregate_by_currency(
            start=start, end=end, currency=currency
        )
        settlement_aggregates = SettlementRepository(session).aggregate_by_currency(
            start=start, end=end, currency=currency
        )

        currencies = sorted(
            {
                *payment_aggregates.keys(),
                *refund_aggregates.keys(),
                *settlement_aggregates.keys(),
            },
            key=lambda c: (c is None, c or ""),
        )

        items = []
        for code in currencies:
            payments = payment_aggregates.get(code)
            refunds = refund_aggregates.get(code)
            settlements = settlement_aggregates.get(code)
            successful_amount = (
                payments.captured_amount_minor if payments else 0
            )
            refunded_amount = refunds.amount_minor if refunds else 0
            items.append(
                FinanceCurrencySummary(
                    currency=code,
                    gross_amount_minor=payments.amount_minor if payments else 0,
                    transaction_count=payments.count if payments else 0,
                    successful_amount_minor=successful_amount,
                    successful_count=payments.captured_count if payments else 0,
                    failed_count=payments.failed_count if payments else 0,
                    refunded_amount_minor=refunded_amount,
                    refund_count=refunds.count if refunds else 0,
                    net_amount_minor=successful_amount - refunded_amount,
                    settlement_amount_minor=(
                        settlements.amount_minor if settlements else 0
                    ),
                )
            )

        return FinanceSummaryResponse(
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            status=status,
            method=method,
            currencies=items,
        )

    # --- paged record lists -------------------------------------------------

    @staticmethod
    def list_payments(
        session: Session,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
        status: str | None = None,
        method: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> FinancePaymentsPage:
        start, end = _utc_day_bounds(start_date, end_date)
        rows, total = PaymentRepository(session).list_filtered(
            start=start,
            end=end,
            currency=currency,
            status=status,
            method=method,
            limit=limit,
            offset=offset,
        )
        return FinancePaymentsPage(
            items=[
                FinancePaymentRecord.model_validate(row, from_attributes=True)
                for row in rows
            ],
            limit=limit,
            offset=offset,
            total=total,
        )

    @staticmethod
    def list_refunds(
        session: Session,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
        status: str | None = None,
        payment_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> FinanceRefundsPage:
        start, end = _utc_day_bounds(start_date, end_date)
        rows, total = RefundRepository(session).list_filtered(
            start=start,
            end=end,
            currency=currency,
            status=status,
            payment_id=payment_id,
            limit=limit,
            offset=offset,
        )
        return FinanceRefundsPage(
            items=[
                FinanceRefundRecord.model_validate(row, from_attributes=True)
                for row in rows
            ],
            limit=limit,
            offset=offset,
            total=total,
        )

    @staticmethod
    def list_settlements(
        session: Session,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> FinanceSettlementsPage:
        start, end = _utc_day_bounds(start_date, end_date)
        rows, total = SettlementRepository(session).list_filtered(
            start=start,
            end=end,
            currency=currency,
            status=status,
            limit=limit,
            offset=offset,
        )
        return FinanceSettlementsPage(
            items=[
                FinanceSettlementRecord.model_validate(row, from_attributes=True)
                for row in rows
            ],
            limit=limit,
            offset=offset,
            total=total,
        )

    @staticmethod
    def list_orders(
        session: Session,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str | None = None,
        status: str | None = None,
        receipt: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> FinanceOrdersPage:
        start, end = _utc_day_bounds(start_date, end_date)
        rows, total = OrderRepository(session).list_filtered(
            start=start,
            end=end,
            currency=currency,
            status=status,
            receipt=receipt,
            limit=limit,
            offset=offset,
        )
        return FinanceOrdersPage(
            items=[
                FinanceOrderRecord.model_validate(row, from_attributes=True)
                for row in rows
            ],
            limit=limit,
            offset=offset,
            total=total,
        )

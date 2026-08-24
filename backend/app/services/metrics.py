"""Finance metrics and read models over the normalized internal records.

Pure read layer: no fetching, no writes, no provider calls, and **no LLM**
anywhere — every number below is a deterministic aggregation over stored
fields, per docs/FINANCE_METRICS_SPECIFICATION.md (the source of truth for
every formula; section numbers in comments refer to it).

Two date conventions live here deliberately:

- ``FinanceService.summary`` keeps its historical contract: ``start``/
  ``end`` ISO calendar dates interpreted as inclusive UTC day bounds
  (documented via the ``timezone`` echo field).
- The metric-grade methods (:meth:`payment_performance`, :meth:`revenue`,
  :meth:`trend`) take explicit tz-aware half-open ``[start, end)``
  datetimes built by :mod:`app.core.periods` in IST (spec §6.2).

Status classification follows spec §2.0: successful = ``captured`` plus
``refunded`` (a refunded payment was necessarily captured first); failed =
``failed``; everything else is in-progress and excluded from both buckets.
Refunds contribute to realized metrics only with ``status == "processed"``
(spec §3.3/§4.1), aggregated from individual refund records so multiple
partial refunds per payment are summed exactly once each (spec §4.4).

All monetary arithmetic is integer minor units; ratios are floats rounded
to two decimals purely for display and are ``None`` when their denominator
is zero (spec Part 6.4) — division by zero can never occur.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.core.periods import REPORTING_TIMEZONE_NAME, query_bounds, trend_pair
from app.db.models.payment import PAYMENT_KNOWN_STATUSES
from app.db.models.refund import REFUND_STATUS_PROCESSED
from app.db.repositories import (
    OrderRepository,
    PaymentAggregate,
    PaymentRepository,
    RefundRepository,
    SettlementAggregate,
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
    MetricRate,
    NetRevenueVariants,
    PaymentPerformanceResponse,
    RefundMetricsBlock,
    RevenueMetricsResponse,
    ReportingPeriod,
    TrendResponse,
    TrendWindow,
)

logger = logging.getLogger(__name__)

MAX_FINANCE_PAGE_LIMIT = 500

# Display precision for percentage values. Percentages are ratios, not
# monetary amounts, so this rounding does not affect any money math.
RATE_DECIMALS = 2


# --- pure ratio / change helpers (independently testable) ------------------


def safe_rate(numerator: int, denominator: int) -> float | None:
    """Percentage of a ratio, or ``None`` when undefined (spec Part 6.4).

    A zero denominator yields ``None`` — never ``0.0``, never an exception.
    """
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, RATE_DECIMALS)


def percentage_change(current: int, previous: int) -> tuple[int, float | None, str]:
    """Absolute + percentage change with spec Part 6.4 semantics.

    Returns ``(absoluteChange, percentageChange, changeType)`` where
    ``changeType`` is one of ``"normal"`` / ``"new_activity"`` /
    ``"no_activity"``. When ``previous == 0`` the percentage is
    mathematically undefined and is returned as ``None`` — never a
    fabricated ``0%`` or ``∞%`` string.
    """
    absolute = current - previous
    if previous == 0:
        if current == 0:
            return absolute, None, "no_activity"
        return absolute, None, "new_activity"
    return absolute, round(absolute / previous * 100, RATE_DECIMALS), "normal"


# --- window aggregation plumbing -------------------------------------------


@dataclass(frozen=True)
class _WindowTotals:
    """One currency's payment/refund/settlement aggregates for a window.

    Refunds are always the realized subset (``status == "processed"``,
    spec §3.3/§4.1). Missing groups degrade to zero-valued aggregates so
    every metric stays well-defined on empty datasets.
    """

    payments: PaymentAggregate
    refunds_count: int
    refunds_amount_minor: int
    settlements: SettlementAggregate

    @classmethod
    def empty(cls) -> "_WindowTotals":
        return cls(
            payments=PaymentAggregate(), refunds_count=0, refunds_amount_minor=0,
            settlements=SettlementAggregate(),
        )


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

        The union of currencies seen in any resource forms the result
        groups; absent sides contribute zeros so the net variants stay
        well-defined. Realized refunds are processed-only (spec §3.3/§4.1).
        """
        start, end = _utc_day_bounds(start_date, end_date)

        payment_repo = PaymentRepository(session)
        payment_aggregates = payment_repo.aggregate_by_currency(
            start=start, end=end, currency=currency, status=status, method=method
        )
        refund_aggregates = RefundRepository(session).aggregate_by_currency(
            start=start, end=end, currency=currency, status=REFUND_STATUS_PROCESSED
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
            payments = payment_aggregates.get(code, PaymentAggregate())
            refunds = refund_aggregates.get(code)
            settlements = settlement_aggregates.get(code, SettlementAggregate())

            gross = payments.successful_amount_minor
            fees = payments.fee_minor_sum
            taxes = payments.tax_minor_sum
            refunded_amount = refunds.amount_minor if refunds else 0
            items.append(
                FinanceCurrencySummary(
                    currency=code,
                    gross_amount_minor=gross,
                    transaction_count=payments.count,
                    successful_amount_minor=gross,  # identical per spec §3.2
                    successful_count=payments.successful_count,
                    failed_count=payments.failed_count,
                    in_progress_count=(
                        payments.count
                        - payments.successful_count
                        - payments.failed_count
                    ),
                    fee_minor=fees,
                    tax_minor=taxes,
                    refunded_amount_minor=refunded_amount,
                    refund_count=refunds.count if refunds else 0,
                    net_of_refunds_minor=gross - refunded_amount,
                    net_of_fees_minor=gross - fees - taxes,
                    net_of_refunds_and_fees_minor=(
                        gross - refunded_amount - fees - taxes
                    ),
                    settlement_amount_minor=settlements.amount_minor,
                    settlement_fees_minor=settlements.fees_minor_sum,
                    settlement_tax_minor=settlements.tax_minor_sum,
                )
            )

        _log_unknown_payment_statuses(payment_repo, start=start, end=end)

        return FinanceSummaryResponse(
            start_date=start_date,
            end_date=end_date,
            timezone="UTC",  # documented interpretation of start/end params
            currency=currency,
            status=status,
            method=method,
            currencies=items,
        )

    # --- metric-grade methods ---------------------------------------------

    @staticmethod
    def payment_performance(
        session: Session,
        *,
        start: datetime,
        end: datetime,
        currency: str = "INR",
    ) -> PaymentPerformanceResponse:
        """Spec Part 2 metrics over one explicit half-open IST window.

        Rates use Transaction Volume as denominator (spec §2.4/§2.5
        recommended basis, labeled in the response) and are ``None`` when
        the period contains no attempts.
        """
        qb_start, qb_end = query_bounds(start, end)
        repo = PaymentRepository(session)
        payments = repo.aggregate_by_currency(
            start=qb_start, end=qb_end, currency=currency
        ).get(currency, PaymentAggregate())
        excluded = _excluded_transaction_count(
            repo.aggregate_by_currency(start=qb_start, end=qb_end), currency
        )
        _log_unknown_payment_statuses(repo, start=qb_start, end=qb_end)

        volume = payments.count
        in_progress = volume - payments.successful_count - payments.failed_count
        return PaymentPerformanceResponse(
            period=ReportingPeriod(start=start, end=end),
            currency=currency,
            transaction_volume=volume,
            successful_transactions=payments.successful_count,
            failed_transactions=payments.failed_count,
            in_progress_transactions=in_progress,
            success_rate=MetricRate(
                value=safe_rate(payments.successful_count, volume),
                basis="transaction_volume",
            ),
            failure_rate=MetricRate(
                value=safe_rate(payments.failed_count, volume),
                basis="transaction_volume",
            ),
            other_currency_transactions_excluded=excluded,
        )

    @staticmethod
    def revenue(
        session: Session,
        *,
        start: datetime,
        end: datetime,
        currency: str = "INR",
    ) -> RevenueMetricsResponse:
        """Spec Part 3 metrics over one explicit half-open IST window."""
        qb_start, qb_end = query_bounds(start, end)
        payment_repo = PaymentRepository(session)
        payments = payment_repo.aggregate_by_currency(
            start=qb_start, end=qb_end, currency=currency
        ).get(currency, PaymentAggregate())
        refunds = RefundRepository(session).aggregate_by_currency(
            start=qb_start, end=qb_end, currency=currency,
            status=REFUND_STATUS_PROCESSED,
        ).get(currency)
        excluded = _excluded_transaction_count(
            payment_repo.aggregate_by_currency(start=qb_start, end=qb_end),
            currency,
        )
        _log_unknown_payment_statuses(payment_repo, start=qb_start, end=qb_end)

        gross = payments.successful_amount_minor
        fees = payments.fee_minor_sum
        taxes = payments.tax_minor_sum
        refund_amount = refunds.amount_minor if refunds else 0
        refund_count = refunds.count if refunds else 0

        return RevenueMetricsResponse(
            period=ReportingPeriod(start=start, end=end),
            currency=currency,
            gross_revenue_minor=gross,
            razorpay_fee_minor=fees,
            razorpay_tax_minor=taxes,
            net_revenue=NetRevenueVariants(
                net_of_refunds_minor=gross - refund_amount,
                net_of_fees_minor=gross - fees - taxes,
                net_of_refunds_and_fees_minor=(
                    gross - refund_amount - fees - taxes
                ),
            ),
            refunds=RefundMetricsBlock(
                refund_count=refund_count,
                refund_amount_minor=refund_amount,
                refund_rate=MetricRate(
                    value=safe_rate(refund_amount, gross),
                    basis="definition_A_refund_amount_over_gross_revenue",
                ),
            ),
            other_currency_transactions_excluded=excluded,
        )

    # --- trends -------------------------------------------------------------

    @staticmethod
    def trend_value(
        session: Session,
        *,
        extractor: Callable[[_WindowTotals], int],
        start: datetime,
        end: datetime,
        currency: str,
    ) -> int:
        """Evaluate one metric extractor over one half-open window."""
        qb_start, qb_end = query_bounds(start, end)
        payments = PaymentRepository(session).aggregate_by_currency(
            start=qb_start, end=qb_end, currency=currency
        ).get(currency, PaymentAggregate())
        refunds = RefundRepository(session).aggregate_by_currency(
            start=qb_start, end=qb_end, currency=currency,
            status=REFUND_STATUS_PROCESSED,
        )
        settlements = SettlementRepository(session).aggregate_by_currency(
            start=qb_start, end=qb_end, currency=currency
        ).get(currency, SettlementAggregate())
        totals = _WindowTotals(
            payments=payments,
            refunds_count=refunds[currency].count if currency in refunds else 0,
            refunds_amount_minor=(
                refunds[currency].amount_minor if currency in refunds else 0
            ),
            settlements=settlements,
        )
        return extractor(totals)

    @staticmethod
    def trend(
        session: Session,
        *,
        metric: str,
        granularity: str,
        now: datetime,
        currency: str = "INR",
    ) -> TrendResponse:
        """Spec Part 6 comparison for one metric over day/week/month pairs.

        ``now`` must be an aware datetime in the reporting timezone; the
        windows come straight from :func:`app.core.periods.trend_pair`.
        """
        extractor = TREND_METRIC_EXTRACTORS[metric]
        (current_start, current_end), (previous_start, previous_end) = trend_pair(
            granularity, now
        )
        current_value = FinanceService.trend_value(
            session,
            extractor=extractor,
            start=current_start,
            end=current_end,
            currency=currency,
        )
        previous_value = FinanceService.trend_value(
            session,
            extractor=extractor,
            start=previous_start,
            end=previous_end,
            currency=currency,
        )
        absolute, percentage, change_type = percentage_change(
            current_value, previous_value
        )
        return TrendResponse(
            metric=metric,
            granularity=granularity,
            timezone=REPORTING_TIMEZONE_NAME,
            current_period=TrendWindow(
                start=current_start, end=current_end, value=current_value
            ),
            previous_period=TrendWindow(
                start=previous_start, end=previous_end, value=previous_value
            ),
            absolute_change=absolute,
            percentage_change=percentage,
            change_type=change_type,
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


# --- module-level helpers ---------------------------------------------------


def _excluded_transaction_count(
    all_currencies: dict[str | None, object], currency: str
) -> int:
    """Count aggregate rows whose stored currency ≠ the requested one.

    Works over any mapping produced by ``aggregate_by_currency``; counts
    records (not amounts) and never merges amounts across currencies
    (spec Part 9).
    """
    return sum(
        agg.count
        for key, agg in all_currencies.items()
        if key != currency and getattr(agg, "count", 0)
    )


def _log_unknown_payment_statuses(
    repo: PaymentRepository,
    *,
    start: datetime | None,
    end: datetime | None,
) -> None:
    """Warn when stored payment statuses fall outside Razorpay's enum.

    Spec Part 11: unrecognized status values are anomalies to log, never
    silently classified into an existing bucket (they remain counted only
    inside transaction volume / in-progress).
    """
    statuses = set(repo.distinct_statuses(start=start, end=end))
    unknown = statuses - PAYMENT_KNOWN_STATUSES
    if unknown:
        logger.warning(
            "Unknown payment status values encountered (%s); treated as "
            "in-progress and excluded from success/failure metrics",
            sorted(str(s) for s in unknown),
        )


def _payment_extractor(field: str) -> Callable[[_WindowTotals], int]:
    def extract(totals: _WindowTotals) -> int:
        return int(getattr(totals.payments, field))

    return extract


def _settlement_extractor(field: str) -> Callable[[_WindowTotals], int]:
    def extract(totals: _WindowTotals) -> int:
        return int(getattr(totals.settlements, field))

    return extract


#: Supported trend metrics (spec Part 6 + Part 7 verified rows). Keys are
#: stable API identifiers; each maps to an exact integer over a window.
TREND_METRIC_EXTRACTORS: dict[str, Callable[[_WindowTotals], int]] = {
    "transaction_volume": _payment_extractor("count"),
    "successful_transactions": _payment_extractor("successful_count"),
    "failed_transactions": _payment_extractor("failed_count"),
    "gross_revenue": _payment_extractor("successful_amount_minor"),
    "razorpay_fee": _payment_extractor("fee_minor_sum"),
    "razorpay_tax": _payment_extractor("tax_minor_sum"),
    "refund_amount": lambda totals: totals.refunds_amount_minor,
    "refund_count": lambda totals: totals.refunds_count,
    "settlement_amount": _settlement_extractor("amount_minor"),
    "settlement_fees": _settlement_extractor("fees_minor_sum"),
    "settlement_tax": _settlement_extractor("tax_minor_sum"),
}


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

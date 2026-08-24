"""Finance read-model schemas (DB-backed views over synced records).

Money rules mirror the rest of the app: every ``*_minor`` field is an exact
integer in the currency's smallest unit — never floating point. Rates and
percentage changes are floats rounded to 2 decimals; they are ratios, not
monetary amounts, and are ``None`` (never 0) when their denominator is zero
(specification Part 6.4).

Metric definitions follow docs/FINANCE_METRICS_SPECIFICATION.md — the
authoritative source of truth:

- ``gross_amount_minor``            §3.1 sum of amounts of captured payments
                                    (incl. refunded-status payments, §2.0/§3.6)
- ``transaction_count``             §2.1 number of payment attempts, any status
- ``successful_count``              §2.2 count of the gross-revenue population
- ``failed_count``                  §2.3 count of ``failed`` payments
- ``in_progress_count``             §2.0 created/authorized/unknown payments
- ``fee_minor`` / ``tax_minor``     §3.5 fee/tax sums over successful payments
- ``refunded_amount_minor``         §3.3 sum of *processed* refunds only
- ``refund_count``                  §4.1 count of *processed* refunds only
- ``net_of_*_minor``                §3.4 three separately-labeled net variants
- ``settlement_amount_minor``       §5.2 direct Settlement-entity field sum
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.periods import REPORTING_TIMEZONE_NAME


class FinanceCurrencySummary(BaseModel):
    """One currency's slice of the finance summary."""

    currency: str | None = Field(
        description="ISO code as stored from the provider (null if absent)"
    )
    gross_amount_minor: int = Field(
        ge=0,
        description=(
            "Spec §3.1: sum of payment amounts over the successful "
            "population (captured + refunded-status), any fees excluded"
        ),
    )
    transaction_count: int = Field(
        ge=0, description="Spec §2.1: all payment attempts in window"
    )
    successful_amount_minor: int = Field(
        ge=0,
        description="Identical to gross_amount_minor per spec §3.2",
    )
    successful_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    in_progress_count: int = Field(
        ge=0,
        description="created/authorized/unknown-status payments (spec §2.0)",
    )
    fee_minor: int = Field(
        ge=0, description="Spec §3.5: SUM(payment.fee) over successful"
    )
    tax_minor: int = Field(
        ge=0, description="Spec §3.5: SUM(payment.tax) over successful"
    )
    refunded_amount_minor: int = Field(
        ge=0,
        description="Spec §3.3: sum of processed refunds only",
    )
    refund_count: int = Field(
        ge=0, description="Spec §4.1: count of processed refunds only"
    )
    net_of_refunds_minor: int = Field(
        description="Spec §3.4: gross − refund amount (may be negative)"
    )
    net_of_fees_minor: int = Field(
        description="Spec §3.4: gross − fees − taxes (may be negative)"
    )
    net_of_refunds_and_fees_minor: int = Field(
        description="Spec §3.4: gross − refunds − fees − taxes"
    )
    settlement_amount_minor: int = Field(ge=0)
    settlement_fees_minor: int = Field(
        ge=0, description="Spec §5.2: direct settlement fees field sum"
    )
    settlement_tax_minor: int = Field(
        ge=0, description="Spec §5.2: direct settlement tax field sum"
    )


class FinanceSummaryResponse(BaseModel):
    """Aggregated finance metrics grouped by currency."""

    start_date: date | None = Field(default=None, description="Inclusive window start")
    end_date: date | None = Field(default=None, description="Inclusive window end")
    timezone: str | None = Field(
        default=None,
        description="Timezone the start/end date bounds are interpreted in",
    )
    currency: str | None = Field(
        default=None, description="Currency filter echo (None = all currencies)"
    )
    status: str | None = None
    method: str | None = None
    currencies: list[FinanceCurrencySummary]


class FinancePaymentRecord(BaseModel):
    """Stored payment row (verbatim, plus local bookkeeping columns)."""

    id: int
    razorpay_payment_id: str
    entity: str | None = None
    amount_minor: int
    currency: str | None = None
    status: str | None = None
    order_id: str | None = None
    invoice_id: str | None = None
    method: str | None = None
    captured: bool | None = None
    fee_minor: int | None = None
    tax_minor: int | None = None
    description: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)
    provider_created_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FinanceRefundRecord(BaseModel):
    """Stored refund row."""

    id: int
    razorpay_refund_id: str
    entity: str | None = None
    razorpay_payment_id: str
    amount_minor: int
    currency: str | None = None
    status: str | None = None
    speed: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)
    provider_created_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FinanceSettlementRecord(BaseModel):
    """Stored settlement row."""

    id: int
    razorpay_settlement_id: str
    entity: str | None = None
    amount_minor: int
    fees_minor: int
    tax_minor: int
    currency: str | None = None
    status: str | None = None
    utr: str | None = None
    provider_created_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FinanceOrderRecord(BaseModel):
    """Stored order row."""

    id: int
    razorpay_order_id: str
    entity: str | None = None
    amount_minor: int
    amount_paid_minor: int
    amount_due_minor: int
    currency: str | None = None
    status: str | None = None
    receipt: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)
    provider_created_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class _FinancePage(BaseModel):
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class FinancePaymentsPage(_FinancePage):
    items: list[FinancePaymentRecord]


class FinanceRefundsPage(_FinancePage):
    items: list[FinanceRefundRecord]


class FinanceSettlementsPage(_FinancePage):
    items: list[FinanceSettlementRecord]


class FinanceOrdersPage(_FinancePage):
    items: list[FinanceOrderRecord]


# --- metric-grade responses (Finance Intelligence Engine) -----------------
#
# Field names use this project's snake_case convention. Percentages are
# floats rounded to 2 decimals and None when undefined; money is always
# exact minor-unit integers.


class MetricRate(BaseModel):
    """A percentage that may be mathematically undefined.

    Spec Part 6.4: a zero denominator yields ``value=None`` — never 0, never
    an exception. ``basis`` labels exactly what was divided by what so the
    definition travels with the number.
    """

    value: float | None = Field(description="Percentage 0–100, or None if undefined")
    basis: str = Field(description="Denominator/definition label")


class ReportingPeriod(BaseModel):
    """Explicit reporting window (specification Part 11 validation rule)."""

    start: datetime = Field(description="Inclusive window start (ISO 8601)")
    end: datetime = Field(description="Exclusive window end (ISO 8601)")
    timezone: str = REPORTING_TIMEZONE_NAME


class PaymentPerformanceResponse(BaseModel):
    """GET /finance/payment-performance — specification Part 2 metrics."""

    period: ReportingPeriod
    currency: str
    transaction_volume: int = Field(ge=0, description="Spec §2.1")
    successful_transactions: int = Field(ge=0, description="Spec §2.2")
    failed_transactions: int = Field(ge=0, description="Spec §2.3")
    in_progress_transactions: int = Field(ge=0, description="Spec §2.0 exclusion")
    success_rate: MetricRate = Field(
        description="§2.4: successful / transaction_volume × 100"
    )
    failure_rate: MetricRate = Field(
        description="§2.5: failed / transaction_volume × 100"
    )
    other_currency_transactions_excluded: int = Field(
        ge=0,
        description=(
            "Payment attempts outside the requested currency, excluded from "
            "every aggregate (spec Part 9; the spec's "
            "nonInrTransactionsExcluded when currency is INR)"
        ),
    )


class NetRevenueVariants(BaseModel):
    """The three §3.4 net-revenue definitions, never collapsed into one."""

    net_of_refunds_minor: int = Field(description="gross − refund amount")
    net_of_fees_minor: int = Field(description="gross − fees − taxes")
    net_of_refunds_and_fees_minor: int = Field(
        description="gross − refund amount − fees − taxes"
    )


class RefundMetricsBlock(BaseModel):
    """Refund metrics for one period (specification Part 4)."""

    refund_count: int = Field(ge=0, description="Spec §4.1: processed only")
    refund_amount_minor: int = Field(ge=0, description="Spec §3.3 framing (a)")
    refund_rate: MetricRate = Field(
        description="§4.3 definition A: refund amount / gross revenue × 100"
    )


class RevenueMetricsResponse(BaseModel):
    """GET /finance/revenue — specification Part 3 metrics.

    Note: "Successful Revenue" is deliberately absent — spec §3.2 defines it
    as identical to Gross Revenue and recommends exposing it once.
    """

    period: ReportingPeriod
    currency: str
    gross_revenue_minor: int = Field(ge=0, description="Spec §3.1")
    razorpay_fee_minor: int = Field(ge=0, description="Spec §3.5")
    razorpay_tax_minor: int = Field(ge=0, description="Spec §3.5")
    net_revenue: NetRevenueVariants = Field(description="Spec §3.4 variants")
    refunds: RefundMetricsBlock
    other_currency_transactions_excluded: int = Field(
        ge=0, description="See PaymentPerformanceResponse (spec Part 9)"
    )


class TrendWindow(BaseModel):
    """One side of a trend comparison: its window and the metric value."""

    start: datetime
    end: datetime
    value: int = Field(ge=0)


ChangeType = Literal["normal", "new_activity", "no_activity"]


class TrendResponse(BaseModel):
    """GET /finance/trends/{metric} — specification Part 6.

    ``absolute_change`` is always defined; ``percentage_change`` is None
    with an explicit ``change_type`` whenever the previous value is zero
    (spec Part 6.4).
    """

    metric: str
    granularity: Literal["day", "week", "month"]
    timezone: str = REPORTING_TIMEZONE_NAME
    current_period: TrendWindow
    previous_period: TrendWindow
    absolute_change: int
    percentage_change: float | None = None
    change_type: ChangeType

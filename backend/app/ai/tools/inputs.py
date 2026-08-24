"""Typed inputs for the Finance Tools layer.

Every tool takes one Pydantic model — the future LangGraph/agent layer
constructs these from LLM-produced arguments, so validation happens here,
at the boundary, before any engine call. Design rules:

- Only parameters the underlying FinanceService method already supports.
  Named periods reuse :mod:`app.core.periods` presets (spec §6.1) and
  trend metrics reuse :data:`app.services.metrics.TREND_METRIC_EXTRACTORS`
  keys — no new period math, no new metric vocabulary.
- The ``Literal`` choices below mirror those existing tuples; a drift
  test in tests/test_ai_tools.py fails if they ever diverge.
- Currency is normalized (strip + upper) exactly like the finance API's
  single-currency guard (spec Part 9); invalid codes raise ``ValueError``
  which surfaces as a standard pydantic ``ValidationError`` and is then
  translated to :class:`InvalidToolInputError` by the tool base.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Mirrors app.api.finance.DEFAULT_METRIC_CURRENCY (kept identical by test;
# importing it here would drag FastAPI into this package).
DEFAULT_TOOL_CURRENCY = "INR"

PeriodName = Literal[
    "today",
    "yesterday",
    "this_week",
    "previous_week",
    "this_month",
    "previous_month",
]

GranularityName = Literal["day", "week", "month"]

TrendMetricName = Literal[
    "transaction_volume",
    "successful_transactions",
    "failed_transactions",
    "gross_revenue",
    "razorpay_fee",
    "razorpay_tax",
    "refund_amount",
    "refund_count",
    "settlement_amount",
    "settlement_fees",
    "settlement_tax",
]


def _normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if not 2 < len(normalized) <= 8:
        raise ValueError("currency must be an ISO code")
    return normalized


class _SingleCurrencyInput(BaseModel):
    """Base for tools that aggregate exactly one currency (spec Part 9)."""

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(
        default=DEFAULT_TOOL_CURRENCY,
        description="Single-currency aggregation filter (never mixed)",
    )

    @field_validator("currency")
    @classmethod
    def _currency_is_normalized(cls, value: str) -> str:
        return _normalize_currency(value)


class RevenueToolInput(_SingleCurrencyInput):
    """Input for the revenue tool."""

    period: PeriodName = Field(
        default="today", description="Named reporting period (IST semantics)"
    )


class PaymentPerformanceToolInput(_SingleCurrencyInput):
    """Input for the payment-performance tool."""

    period: PeriodName = Field(default="today")


class RefundToolInput(_SingleCurrencyInput):
    """Input for the refund-metrics tool."""

    period: PeriodName = Field(default="today")


class TrendToolInput(_SingleCurrencyInput):
    """Input for the trend-comparison tool."""

    metric: TrendMetricName
    granularity: GranularityName = Field(default="day")


class SettlementToolInput(BaseModel):
    """Input for the settlement-aggregates tool.

    Mirrors the finance summary window: inclusive UTC calendar dates.
    ``currency`` is an optional filter — when omitted, every stored
    currency is returned as its own group (engine behavior).
    """

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = Field(default=None)
    end_date: date | None = Field(default=None)
    currency: str | None = Field(default=None)

    @field_validator("currency")
    @classmethod
    def _optional_currency(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _normalize_currency(value)

    @model_validator(mode="after")
    def _date_order(self) -> "SettlementToolInput":
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date must be less than or equal to end_date")
        return self


class FinancialSummaryToolInput(SettlementToolInput):
    """Input for the overall financial-summary tool.

    Adds the payment-level filters the engine summary already supports;
    date/currency rules are inherited unchanged.
    """

    status: str | None = Field(default=None, max_length=32)
    method: str | None = Field(default=None, max_length=32)

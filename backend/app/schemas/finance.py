"""Finance read-model schemas (DB-backed views over synced records).

Money rules mirror the rest of the app: every ``*_minor`` field is an exact
integer in the currency's smallest unit — never floating point.

Metric definitions (per currency, over the filtered window):

- ``gross_amount_minor``      sum of all payment amounts (any status)
- ``transaction_count``       number of payments
- ``successful_amount_minor`` sum of payments with status ``captured``
- ``successful_count``        count of ``captured`` payments
- ``failed_count``            count of payments with status ``failed``
- ``refunded_amount_minor``   sum of refund amounts
- ``refund_count``            number of refunds
- ``net_amount_minor``        successful − refunded (can go negative)
- ``settlement_amount_minor`` sum of settlement amounts
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class FinanceCurrencySummary(BaseModel):
    """One currency's slice of the finance summary."""

    currency: str | None = Field(
        description="ISO code as stored from the provider (null if absent)"
    )
    gross_amount_minor: int = Field(ge=0)
    transaction_count: int = Field(ge=0)
    successful_amount_minor: int = Field(ge=0)
    successful_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    refunded_amount_minor: int = Field(ge=0)
    refund_count: int = Field(ge=0)
    net_amount_minor: int = Field(
        description="successful − refunded; may be negative"
    )
    settlement_amount_minor: int = Field(ge=0)


class FinanceSummaryResponse(BaseModel):
    """Aggregated finance metrics grouped by currency."""

    start_date: date | None = Field(default=None, description="Inclusive window start")
    end_date: date | None = Field(default=None, description="Inclusive window end")
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

"""Financial signal schemas — deterministic findings about metric changes.

A :class:`FinancialSignal` is produced by the Signal Analysis Engine
(``app.services.signal_analysis``) from metrics the Finance Intelligence
Engine already computed. It records a FACT (numbers + a factual evidence
sentence) — never a cause. The LLM may later discuss possible causes, but
every hypothesis it offers must be grounded in these signals.

All fields are JSON-native; ``model_dump(mode="json")`` yields plain
dicts safe for LangGraph state and LLM prompts. Money stays in exact
minor-unit integers; rates are the engine's 2-decimal percentages.
"""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
Direction = Literal["UP", "DOWN"]
SignalUnit = Literal["minor_units", "count", "percent"]

SignalType = Literal[
    # revenue
    "REVENUE_DECLINE",
    "REVENUE_GROWTH",
    # payment performance
    "PAYMENT_SUCCESS_LOW",
    "PAYMENT_FAILURE_HIGH",
    "FAILURE_COUNT_INCREASE",
    "VOLUME_DECLINE",
    "VOLUME_GROWTH",
    # refunds
    "REFUND_RATE_HIGH",
    "REFUND_AMOUNT_INCREASE",
    "REFUND_AMOUNT_DECREASE",
    "REFUND_COUNT_INCREASE",
    "UNUSUAL_REFUND_ACTIVITY",
    # settlements
    "SETTLEMENT_GAP",
    "SETTLEMENT_AMOUNT_INCREASE",
    "SETTLEMENT_AMOUNT_DECREASE",
    # multi-metric relationships (co-occurrence only — never causation)
    "REVENUE_PAYMENT_COINCIDENCE",
]


class FinancialSignal(BaseModel):
    """One deterministic business signal over existing engine metrics."""

    model_config = {"frozen": True}

    #: Stable deduplication/identity key: "<signal_type>:<metric>" plus
    #: currency scope where one applies.
    key: str = Field(description="Deterministic identity of this signal")
    signal_type: SignalType
    metric: str = Field(
        description="Engine metric this signal is about (e.g. gross_revenue)"
    )
    severity: Severity
    unit: SignalUnit
    direction: Direction | None = Field(
        default=None,
        description="For change signals; None for level checks",
    )
    current_value: float | None = Field(
        default=None, description="Latest engine value (exact units)"
    )
    previous_value: float | None = Field(
        default=None,
        description="Prior-period engine value; None for level checks "
        "(never fabricated)",
    )
    absolute_change: float | None = None
    percentage_change: float | None = Field(
        default=None,
        description="Engine-computed percentage when defined; None when "
        "the previous value was zero (spec Part 6.4) or for level checks",
    )
    evidence: str = Field(
        description="Factual sentence quoting engine numbers only — no causes"
    )

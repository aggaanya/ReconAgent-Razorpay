"""Schemas for the AI endpoints (``POST /api/v1/ai/chat`` and
``POST /api/v1/ai/reconcile``).

Both responses follow one contract: FACTS (deterministic numbers produced
by engines/tools) travel in dedicated fields, WORDS (the LLM's narrative)
in ``answer``, and degradation in ``status``/``errors``. No financial
value is ever created in this layer.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.reconciliation import (
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    ReconcileSourceName,
    ReconciliationResult,
    ExceptionSummary,
    ReconciliationDriftAnalysis,
    ReconciliationSummary,
)

#: Hard cap on question length. The question is embedded verbatim in LLM
#: prompts (planner + interpreter); a bound keeps prompt size and abuse
#: surface finite while being generous for finance questions.
MAX_QUESTION_LENGTH = 1000


class AiChatRequest(BaseModel):
    """One natural-language finance question."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTION_LENGTH,
        description=(
            "Natural-language finance question, e.g. 'Why did my revenue "
            "decrease this week?'"
        ),
        examples=["What was my revenue this month?"],
    )

    @field_validator("question")
    @classmethod
    def _require_content(cls, value: str) -> str:
        """Reject whitespace-only questions; otherwise keep input verbatim
        apart from leading/trailing whitespace."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("must contain non-whitespace content")
        return stripped


class AiChatResponse(BaseModel):
    """Outcome of one finance intelligence run over the user's question.

    ``status`` semantics (from the graph): ``completed`` (data retrieved
    and interpreted), ``partial`` (data retrieved but some tools or the
    interpretation failed), ``failed`` (nothing could be retrieved).
    """

    question: str
    status: Literal["completed", "partial", "failed"]
    #: LLM narrative about the retrieved facts; ``None`` when the
    #: interpretation step failed (the facts are still returned).
    answer: str | None = None
    selected_tools: list[str] = Field(default_factory=list)
    selection_source: str = ""
    #: Verbatim Finance Tool envelopes — deterministic facts only.
    tool_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tool_errors: dict[str, str] = Field(default_factory=dict)
    #: Deterministic findings from the Signal Analysis Engine.
    financial_signals: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AiReconcileRequest(BaseModel):
    """Request for one deterministic reconciliation run.

    Mirrors :class:`~app.ai.tools.reconciliation.ReconcileToolInput` so
    the HTTP boundary validates the exact same constraints the tool
    enforces. Only dataset selection is accepted — never record contents
    (unknown fields are rejected, not silently ignored).
    """

    model_config = ConfigDict(extra="forbid")

    source: ReconcileSourceName = Field(
        default="synthetic",
        description="Dataset source; 'synthetic' is the Track 04 batch",
    )
    seed: int = Field(default=42, ge=0, description="Generator seed")
    size: int = Field(
        default=100,
        ge=50,
        le=5_000,
        description="Batch size in cases (Track 04 requires 50+)",
    )
    max_settlement_delay_days: int = Field(
        default=DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
        ge=0,
        description=(
            "Settlement-delay tolerance in days; later settlements are "
            "flagged SETTLEMENT_DELAY"
        ),
    )
    explain: bool = Field(
        default=True,
        description="When true, attach an LLM narrative of the result",
    )
    question: str | None = Field(
        default=None,
        max_length=MAX_QUESTION_LENGTH,
        description=(
            "Optional focus prompt guiding the LLM narrative; facts are "
            "never altered by it"
        ),
    )

    @field_validator("question")
    @classmethod
    def _require_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must contain non-whitespace content")
        return stripped


class AiReconcileResponse(BaseModel):
    """Deterministic reconciliation outcome plus optional LLM narrative.

    ``accuracy`` is ``None`` at runtime by design: ground truth exists
    only inside the evaluation harness (generator/tests/benchmark), never
    in the serving path — reporting a fabricated accuracy would defeat
    its purpose. Run ``scripts/reconcile_benchmark.py`` for measured
    accuracy against ground truth.
    """

    status: Literal["completed", "partial", "failed"]
    answer: str | None = None
    total_records: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    exception_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    match_rate: float | None = None
    accuracy: float | None = None
    processing_time_ms: float = Field(ge=0)
    throughput_records_per_second: float = Field(ge=0)
    exception_breakdown: dict[str, int] = Field(default_factory=dict)
    exception_summary: ExceptionSummary | None = None
    exceptions: list[ReconciliationResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AiReconcileCompareRequest(BaseModel):
    """Request for What-Changed comparison between two reconciliation runs."""

    previous_seed: int = Field(default=41, ge=0, description="Previous run seed")
    previous_size: int = Field(
        default=100, ge=50, le=5000, description="Previous batch size"
    )
    current_seed: int = Field(default=42, ge=0, description="Current run seed")
    current_size: int = Field(
        default=100, ge=50, le=5000, description="Current batch size"
    )
    explain: bool = Field(
        default=False,
        description="Optionally attach an LLM drift explanation",
    )


class AiReconcileCompareResponse(BaseModel):
    """Deterministic comparison plus an optional safe narrative rendering."""

    drift: ReconciliationDriftAnalysis
    previous_summary: ReconciliationSummary
    current_summary: ReconciliationSummary
    answer: str | None = None
    errors: list[str] = Field(default_factory=list)

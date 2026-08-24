"""Reconciliation domain schemas — Track 04 multi-source finance loop.

Layer position: pure Pydantic domain models consumed by the deterministic
reconciliation engine (``app.services.reconciliation``), the synthetic
dataset generator (``app.services.reconciliation_synthetic``) and the
``reconcile_transactions`` Finance Tool. No LLM, no database, no provider
calls anywhere in this vocabulary.

Money follows the project-wide convention: exact minor-unit integers,
single-currency comparisons only. A settlement side is modeled as
**payment-attributed settlement lines** (how Razorpay's recon breakup
attributes individual payments to a settlement batch) — the batch-level
DB ``Settlement`` rows carry no payment reference, so per-payment
reconciliation inputs are line records, not DB rows.

Ground truth (expected outcomes for evaluation) is deliberately defined
in the generator module, never here: the engine cannot see it.
"""

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.signals import Severity

#: Dataset sources accepted by reconcile entry points (Track 04 batch).
ReconcileSourceName = Literal["synthetic"]


class ReconciliationStatus(str, Enum):
    """Deterministic outcome of one reconciliation case.

    Every value except ``MATCHED`` is an exception; ``UNRESOLVED`` marks
    cases where no safe deterministic classification exists (the engine
    refuses to guess rather than fabricating a verdict).

    ``AMOUNT_MISMATCH`` vs ``UNEXPLAINED_SETTLEMENT_DIFFERENCE``: the
    former is a raw gross-vs-settled difference on a case with **no**
    financial components available (legacy shape), the latter means the
    engine subtracted the available fee/tax/refund components into an
    expected settlement and the actual settlement still differed.
    """

    MATCHED = "MATCHED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    UNEXPLAINED_SETTLEMENT_DIFFERENCE = "UNEXPLAINED_SETTLEMENT_DIFFERENCE"
    MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
    MISSING_PAYMENT = "MISSING_PAYMENT"
    DUPLICATE_SETTLEMENT = "DUPLICATE_SETTLEMENT"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    INVALID_STATUS = "INVALID_STATUS"
    REFUND_MISMATCH = "REFUND_MISMATCH"
    SETTLEMENT_DELAY = "SETTLEMENT_DELAY"
    UNRESOLVED = "UNRESOLVED"


#: Statuses that count toward ``exception_count`` (everything but MATCHED).
EXCEPTION_STATUSES: frozenset[ReconciliationStatus] = frozenset(
    status
    for status in ReconciliationStatus
    if status is not ReconciliationStatus.MATCHED
)

#: Payment statuses eligible to appear in a settlement (spec §2.0:
#: refunded payments were necessarily captured first).
SETTLEMENT_ELIGIBLE_STATUSES: frozenset[str] = frozenset(
    {"captured", "refunded"}
)

#: Razorpay payment status vocabulary the engine recognizes (aligned with
#: app.db.models.payment.PAYMENT_KNOWN_STATUSES; kept local so this module
#: stays database-free — a drift test pins the two sets together).
KNOWN_PAYMENT_STATUSES: frozenset[str] = frozenset(
    {"created", "authorized", "captured", "refunded", "failed"}
)

#: Refund status whose money has actually moved (aligned with
#: app.db.models.refund.REFUND_STATUS_PROCESSED; kept local per the
#: database-free rule above). Only processed refunds debit settlements.
REFUND_STATUS_PROCESSED = "processed"

#: Default settlement-delay tolerance in days for the timing-aware rule.
#: Zero means same-day settlement is expected to be within tolerance only
#: when explicitly configured; ``None`` disables timing checks entirely
#: (backward-compatible default). Deliberately NOT presented as a
#: Razorpay fact — merchants configure payout schedules individually, so
#: callers may override it everywhere the engine runs.
DEFAULT_MAX_SETTLEMENT_DELAY_DAYS = 3


class ReconciliationPayment(BaseModel):
    """One payment-side record participating in reconciliation.

    ``fee_minor`` / ``tax_minor`` mirror the documented Razorpay payment
    entity fields already persisted on the DB ``Payment`` row; ``None``
    means the provider did not report them (the engine then reconciles
    against the gross amount — legacy behavior). ``created_on`` enables
    timing-aware rules when both sides carry dates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_id: str = Field(min_length=1, description="Provider payment id")
    amount_minor: int = Field(ge=0, description="Gross amount, minor units")
    currency: str = Field(min_length=1, max_length=8)
    status: str = Field(min_length=1, max_length=32)
    fee_minor: int | None = Field(
        default=None, ge=0, description="Recorded provider fee, minor units"
    )
    tax_minor: int | None = Field(
        default=None,
        ge=0,
        description="Tax levied on the provider fee, minor units",
    )
    created_on: date | None = Field(
        default=None, description="Payment creation date (provider clock)"
    )

    @field_validator("payment_id", "currency", "status")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ReconciliationRefund(BaseModel):
    """One refund record (first-class reconciliation input).

    Only refunds whose ``status`` is ``REFUND_STATUS_PROCESSED`` moved
    money and participate in the expected-settlement debit; every other
    status is inert. A processed refund referencing an unknown payment is
    reported as a MISSING_PAYMENT case anchored on the refund id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    refund_id: str = Field(min_length=1)
    payment_id: str | None = Field(
        default=None, description="Parent payment reference"
    )
    amount_minor: int = Field(ge=0, description="Refunded amount, minor units")
    currency: str = Field(min_length=1, max_length=8)
    status: str = Field(min_length=1, max_length=32)

    @field_validator("refund_id", "currency", "status")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ReconciliationSettlementLine(BaseModel):
    """One payment-attributed settlement line (recon-breakup shape).

    ``payment_id`` is the attribution reference linking the settled money
    back to the payment that produced it. ``settled_on`` enables the
    configurable settlement-delay rule when paired with a payment date.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    settlement_id: str = Field(min_length=1, description="Settlement line id")
    payment_id: str | None = Field(
        default=None,
        description="Attribution reference to the settled payment",
    )
    amount_minor: int = Field(ge=0, description="Settled amount, minor units")
    currency: str = Field(min_length=1, max_length=8)
    settled_on: date | None = Field(
        default=None, description="Settlement date (provider clock)"
    )

    @field_validator("settlement_id", "currency")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ReconciliationResult(BaseModel):
    """One deterministic reconciliation decision.

    ``exception_type`` mirrors ``status`` for every non-matching outcome
    and is ``None`` for ``MATCHED`` — consumers can filter exceptions
    without knowing the status vocabulary.

    Amount semantics: ``expected_amount_minor`` is the **expected
    settlement** (gross minus available fee/tax/processed-refund
    components), ``actual_amount_minor`` is what actually settled, and
    the component fields expose exactly which deterministic inputs
    produced that expectation so an LLM can explain the arithmetic
    without recomputing it.
    """

    model_config = ConfigDict(frozen=True)

    source_transaction_id: str = Field(
        description=(
            "Anchor record id: the payment id for payment-driven cases, "
            "the settlement line id when the payment side is missing, "
            "the refund id for orphaned refunds"
        )
    )
    matched_transaction_id: str | None = Field(
        default=None,
        description="Counterpart id used for the decision (None when absent)",
    )
    status: ReconciliationStatus
    #: Additional detected issues that did not win primary classification.
    #: The first-hit rule keeps exactly one terminal ``status`` (the API
    #: contract stays single-status); genuinely independent findings —
    #: currently the settlement-delay check — are preserved here so a
    #: transaction can carry "unexplained difference AND late settlement"
    #: without a second result row. Empty for legacy shapes.
    secondary_issues: tuple[ReconciliationStatus, ...] = ()
    expected_amount_minor: int | None = None
    actual_amount_minor: int | None = None
    difference_minor: int | None = Field(
        default=None,
        description="actual − expected in minor units; set on mismatches only",
    )
    currency: str | None = None
    reason: str = Field(description="Deterministic rule that decided the case")
    exception_type: ReconciliationStatus | None = None
    #: Deterministic components behind the expectation (None when absent).
    gross_amount_minor: int | None = Field(
        default=None, description="Payment gross amount, minor units"
    )
    fee_minor: int | None = Field(
        default=None, description="Recorded provider fee included in math"
    )
    tax_minor: int | None = Field(
        default=None, description="Recorded fee tax included in math"
    )
    refunded_total_minor: int | None = Field(
        default=None, description="Sum of processed refunds debited"
    )
    #: Deterministic triage annotations — pure policy output from
    #: app.services.reconciliation_policy (never LLM, never DB).
    severity: Severity | None = Field(
        default=None,
        description=(
            "Deterministic triage severity on the shared signal "
            "taxonomy (INFO/LOW/MEDIUM/HIGH/CRITICAL); None when the "
            "result was produced without triage annotation"
        ),
    )
    priority: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Documented bounded triage rank (CRITICAL=100, HIGH=80, "
            "MEDIUM=60, LOW=30, INFO=10); deterministic policy, not a "
            "learned risk score"
        ),
    )
    recommended_action: str | None = Field(
        default=None,
        description=(
            "Deterministic operator guidance for exceptions; None for "
            "MATCHED (nothing to investigate)"
        ),
    )

    @property
    def is_exception(self) -> bool:
        return self.status is not ReconciliationStatus.MATCHED


class ReconciliationSummary(BaseModel):
    """Batch-level deterministic metrics (no ground truth involved).

    Rates are percentages rounded to two decimals purely for display;
    ``accuracy`` requires ground truth and is ``None`` when none was
    supplied — never fabricated.
    """

    total_records: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    exception_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    match_rate: float | None = Field(
        description="matched / total * 100 (2 decimals); None for an "
        "empty batch (spec Part 6.4 zero-denominator convention)"
    )
    accuracy: float | None = Field(
        default=None,
        description="correct decisions / total * 100 vs ground truth; "
        "None without ground truth",
    )
    processing_time_ms: float = Field(ge=0)
    throughput_records_per_second: float = Field(
        description="total / processing seconds (2 decimals)"
    )
    exception_breakdown: dict[str, int] = Field(default_factory=dict)


class ReconciliationReport(BaseModel):
    """Full structured outcome of one reconciliation run."""

    summary: ReconciliationSummary
    #: Every decision, matched or not (audit trail; small batches only).
    results: list[ReconciliationResult] = Field(default_factory=list)
    #: The exception deliverable — every non-MATCHED decision verbatim.
    exceptions: list[ReconciliationResult] = Field(default_factory=list)


class GroundTruthEntry(BaseModel):
    """Expected outcome of one synthetic case (evaluation only).

    Beyond the terminal status, entries may pin the expected arithmetic
    (expected settlement, actual settlement, exception type) and a
    reason fragment. The evaluator checks status strictly, amounts
    exactly when present, and verifies reasons are present on both sides
    — it never compares prose verbatim.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    payment_id: str | None = None
    settlement_ids: tuple[str, ...] = ()
    expected_status: ReconciliationStatus
    #: ``None`` for MATCHED (mirrors ReconciliationResult.exception_type).
    expected_exception_type: ReconciliationStatus | None = None
    #: Authored expectation for compound findings (empty = none expected).
    expected_secondary_issues: tuple[ReconciliationStatus, ...] = ()
    expected_amount_minor: int | None = None
    actual_amount_minor: int | None = None
    #: Reason fragment that must appear in the engine's reason string.
    expected_reason_fragment: str | None = None


class GroundTruthEvaluation(BaseModel):
    """Engine-vs-truth comparison (never an input to the engine).

    Detection metrics treat "is an exception" as the positive class:
    false positives are matched-by-truth cases the engine flagged;
    false negatives are truth-exception cases the engine matched.
    Precision/recall/F1 use the zero-denominator-None convention.
    """

    total_cases: int = Field(ge=0)
    correct_decisions: int = Field(ge=0)
    incorrect_decisions: int = Field(ge=0)
    accuracy: float | None = Field(
        description="correct / total * 100 (2 decimals); None when there "
        "are no cases"
    )
    mismatches: list[dict[str, str]] = Field(default_factory=list)
    true_positives: int = Field(
        default=0,
        description="engine exception where truth also expects an exception",
    )
    false_positives: int = Field(
        default=0,
        description="engine exception where truth expects MATCHED",
    )
    false_negatives: int = Field(
        default=0, description="engine MATCHED where truth expects an exception"
    )
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None


class EvaluationDataset(BaseModel):
    """Identity of the evaluated batch (evaluation-only surface)."""

    source: ReconcileSourceName = "synthetic"
    seed: int = Field(ge=0)
    size: int = Field(ge=0)


class ReconciliationEvaluationReport(BaseModel):
    """MEASURED quality of one reconciliation run vs ground truth.

    EVALUATION/BENCHMARK ONLY — this model exists solely for the
    evaluation harness and ``GET /api/v1/ai/reconcile/evaluation``.
    The serving endpoint (``POST /api/v1/ai/reconcile``) never touches
    ground truth and reports ``accuracy=null``; here accuracy, precision,
    recall and F1 are measured against the generator's isolated
    expectations. Only aggregate metrics and mismatch case ids are
    exposed — never raw ground-truth records.
    """

    surface: Literal["evaluation"] = Field(
        default="evaluation",
        description="Marks this payload as evaluation-only output",
    )
    dataset: EvaluationDataset
    total_records: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    exception_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    match_rate: float | None = Field(
        description="matched / total * 100 (2 decimals)"
    )
    accuracy: float | None = Field(
        description="correct decisions / total * 100 vs ground truth"
    )
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    true_positives: int = Field(ge=0, default=0)
    false_positives: int = Field(ge=0, default=0)
    false_negatives: int = Field(ge=0, default=0)
    throughput_records_per_second: float = Field(ge=0)
    exception_breakdown: dict[str, int] = Field(default_factory=dict)
    mismatches: list[dict[str, str]] = Field(
        default_factory=list,
        description="Per-case deviations from truth (normally empty); "
        "case ids/kinds only — never ground-truth records",
    )


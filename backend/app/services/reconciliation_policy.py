"""Deterministic triage policy for reconciliation exceptions.

Severity, priority and recommended actions are POLICY DECISIONS made by
this module — pure functions over the engine's typed outcomes. No LLM,
no database, no provider access, no financial arithmetic: the numbers
were already settled by the deterministic reconciliation engine, this
layer only ranks and routes them for a finance operator.

Reuse contract: the single severity vocabulary is
``app.schemas.signals.Severity`` (INFO/LOW/MEDIUM/HIGH/CRITICAL) — the
same taxonomy the Signal Analysis Engine uses. A second enum would be a
conflicting source of truth and is deliberately not introduced.

Priority is a documented bounded triage rank (not a learned risk score):
CRITICAL=100, HIGH=80, MEDIUM=60, LOW=30, INFO=10.

Rationale for the mapping (domain reasoning, stable by design):

- CRITICAL — money-integrity violations needing immediate attention:
  DUPLICATE_SETTLEMENT (possible double payout) and REFUND_MISMATCH
  (processed refunds violate integrity vs the payment).
- HIGH — money is provably wrong or unattributable: MISSING_SETTLEMENT,
  MISSING_PAYMENT, CURRENCY_MISMATCH, AMOUNT_MISMATCH,
  UNEXPLAINED_SETTLEMENT_DIFFERENCE, and UNRESOLVED (the engine refuses
  to guess, so a human must review — existing project semantics).
- MEDIUM — records are internally inconsistent but no money movement is
  proven wrong: INVALID_STATUS (a settlement references an ineligible
  payment state).
- LOW — timing-only findings where the amount is perfect:
  SETTLEMENT_DELAY.
- INFO — matched records carry INFO/10 so every row is rankable, but no
  recommended action: there is nothing to investigate.
"""

from app.schemas.reconciliation import (
    EXCEPTION_STATUSES,
    ReconciliationResult,
    ReconciliationStatus,
)
from app.schemas.signals import Severity

#: Deterministic severity per exception type (complete over exceptions).
SEVERITY_BY_EXCEPTION: dict[ReconciliationStatus, Severity] = {
    ReconciliationStatus.DUPLICATE_SETTLEMENT: "CRITICAL",
    ReconciliationStatus.REFUND_MISMATCH: "CRITICAL",
    ReconciliationStatus.MISSING_SETTLEMENT: "HIGH",
    ReconciliationStatus.MISSING_PAYMENT: "HIGH",
    ReconciliationStatus.CURRENCY_MISMATCH: "HIGH",
    ReconciliationStatus.AMOUNT_MISMATCH: "HIGH",
    ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE: "HIGH",
    ReconciliationStatus.UNRESOLVED: "HIGH",
    ReconciliationStatus.INVALID_STATUS: "MEDIUM",
    ReconciliationStatus.SETTLEMENT_DELAY: "LOW",
}

#: Bounded triage scale (documented constants, not learned weights).
PRIORITY_BY_SEVERITY: dict[Severity, int] = {
    "CRITICAL": 100,
    "HIGH": 80,
    "MEDIUM": 60,
    "LOW": 30,
    "INFO": 10,
}

#: Operator guidance per exception type (complete over exceptions).
#: Wording rules: say what to investigate next; never claim an action
#: was already performed; never invent financial facts.
ACTION_BY_EXCEPTION: dict[ReconciliationStatus, str] = {
    ReconciliationStatus.DUPLICATE_SETTLEMENT: (
        "Review the settlement batches referencing this payment and "
        "confirm with the provider which settlement record is valid "
        "before writing off or reversing anything."
    ),
    ReconciliationStatus.REFUND_MISMATCH: (
        "Review the processed refund records against the original "
        "payment and confirm each refund's amount and currency are "
        "correctly associated before adjusting your books."
    ),
    ReconciliationStatus.MISSING_SETTLEMENT: (
        "Verify the settlement batch covering this payment's date and "
        "confirm whether settlement is still pending or was skipped."
    ),
    ReconciliationStatus.MISSING_PAYMENT: (
        "Locate the payment referenced by this settlement line in your "
        "records; if it does not exist, verify the reference id with "
        "the provider before accepting the payout."
    ),
    ReconciliationStatus.CURRENCY_MISMATCH: (
        "Verify the transaction and settlement currencies and check "
        "for a currency conversion or mapping issue in your pipeline."
    ),
    ReconciliationStatus.AMOUNT_MISMATCH: (
        "Compare the settlement amount against the payment, refund, "
        "fee and tax components to identify which component differs."
    ),
    ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE: (
        "Compare the settlement amount against the recorded refund, "
        "fee and tax components; escalate any residual difference to "
        "the provider for itemization."
    ),
    ReconciliationStatus.UNRESOLVED: (
        "Review the underlying payment and settlement records manually "
        "— the system cannot safely classify this case."
    ),
    ReconciliationStatus.INVALID_STATUS: (
        "Verify the payment's lifecycle status with the provider; a "
        "settlement exists for a payment state that should never "
        "settle."
    ),
    ReconciliationStatus.SETTLEMENT_DELAY: (
        "Check the settlement processing status and expected timeline "
        "for this payment before escalating."
    ),
}


def severity_for(status: ReconciliationStatus) -> Severity:
    """Deterministic severity for any terminal status."""
    if status is ReconciliationStatus.MATCHED:
        return "INFO"
    return SEVERITY_BY_EXCEPTION[status]


#: Documented impact-aware priority constants.
IMPACT_BOOST_UNIT_MINOR = 10_000  # ₹100 in minor units per priority point
MAX_INTRA_BAND_BOOST = 15  # Cap intra-band boost so HIGH (80..95) never touches CRITICAL (100)


def priority_for(
    status: ReconciliationStatus,
    financial_impact_minor: int | None = None,
) -> int:
    """Deterministic bounded triage priority for any terminal status.

    Base rank comes from severity (CRITICAL=100, HIGH=80, MEDIUM=60, LOW=30, INFO=10).
    An intra-band boost (+1 per ₹100 of exposure, max +15) ranks higher-impact exceptions
    first within the same severity band while preserving strict band order.
    """
    base = PRIORITY_BY_SEVERITY[severity_for(status)]
    if status is ReconciliationStatus.MATCHED or financial_impact_minor is None or financial_impact_minor <= 0:
        return base
    if base >= 100:
        return 100
    boost = min(int(financial_impact_minor // IMPACT_BOOST_UNIT_MINOR), MAX_INTRA_BAND_BOOST)
    return min(base + boost, 99)


def compute_financial_impact(
    result: ReconciliationResult,
) -> tuple[int | None, str | None]:
    """Deterministic financial impact and explanation for an exception.

    Uses existing engine fields (expected, actual, difference, gross) to calculate
    financial exposure without double counting or inventing values.
    Returns (financial_impact_minor, financial_impact_reason).
    """
    if result.status is ReconciliationStatus.MATCHED:
        return None, None

    if result.status in (
        ReconciliationStatus.AMOUNT_MISMATCH,
        ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE,
    ):
        diff = abs(result.difference_minor) if result.difference_minor is not None else (result.expected_amount_minor or 0)
        reason = (
            f"Residual unexplained settlement difference of {diff} minor units"
            if result.status is ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
            else f"Gross amount mismatch of {diff} minor units"
        )
        return diff, reason

    if result.status is ReconciliationStatus.MISSING_SETTLEMENT:
        exp = result.expected_amount_minor if result.expected_amount_minor is not None else (result.gross_amount_minor or 0)
        return exp, f"Expected settlement exposure of {exp} minor units uncollected"

    if result.status is ReconciliationStatus.MISSING_PAYMENT:
        act = result.actual_amount_minor if result.actual_amount_minor is not None else 0
        return act, f"Unattributable settlement payout of {act} minor units"

    if result.status is ReconciliationStatus.DUPLICATE_SETTLEMENT:
        act = result.actual_amount_minor if result.actual_amount_minor is not None else (result.expected_amount_minor or 0)
        return act, f"Duplicate settlement payout exposure of {act} minor units"

    if result.status is ReconciliationStatus.CURRENCY_MISMATCH:
        exp = result.expected_amount_minor if result.expected_amount_minor is not None else (result.actual_amount_minor or 0)
        return exp, f"Cross-currency transaction exposure of {exp} minor units"

    if result.status is ReconciliationStatus.REFUND_MISMATCH:
        exp = result.expected_amount_minor if result.expected_amount_minor is not None else (result.gross_amount_minor or 0)
        return exp, f"Refund integrity discrepancy exposure of {exp} minor units"

    if result.status is ReconciliationStatus.INVALID_STATUS:
        val = result.actual_amount_minor if result.actual_amount_minor is not None else (result.expected_amount_minor or 0)
        return val, f"Ineligible status settlement exposure of {val} minor units"

    if result.status is ReconciliationStatus.SETTLEMENT_DELAY:
        return 0, "Settlement delay only; 0 financial exposure"

    if result.status is ReconciliationStatus.UNRESOLVED:
        val = result.actual_amount_minor if result.actual_amount_minor is not None else (result.expected_amount_minor or 0)
        return val, f"Unresolved exception exposure of {val} minor units"

    return None, None


def recommended_action_for(status: ReconciliationStatus) -> str | None:
    """Operator action for exceptions; ``None`` for MATCHED (nothing to do)."""
    if status is ReconciliationStatus.MATCHED:
        return None
    return ACTION_BY_EXCEPTION[status]


def annotate_triage(
    results: list[ReconciliationResult],
) -> list[ReconciliationResult]:
    """Return copies of ``results`` annotated with severity/priority/action/impact.

    Pure annotation of engine output — statuses, amounts, reasons and
    ordering are untouched. Frozen models are copied, never mutated.
    """
    annotated: list[ReconciliationResult] = []
    for result in results:
        impact_minor, impact_reason = compute_financial_impact(result)
        severity = severity_for(result.status)
        priority = priority_for(result.status, impact_minor)
        action = recommended_action_for(result.status)
        annotated.append(
            result.model_copy(
                update={
                    "severity": severity,
                    "priority": priority,
                    "recommended_action": action,
                    "financial_impact_minor": impact_minor,
                    "financial_impact_reason": impact_reason,
                }
            )
        )
    return annotated


__all__ = [
    "ACTION_BY_EXCEPTION",
    "IMPACT_BOOST_UNIT_MINOR",
    "MAX_INTRA_BAND_BOOST",
    "PRIORITY_BY_SEVERITY",
    "SEVERITY_BY_EXCEPTION",
    "annotate_triage",
    "compute_financial_impact",
    "priority_for",
    "recommended_action_for",
    "severity_for",
]


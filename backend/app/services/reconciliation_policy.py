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


def priority_for(status: ReconciliationStatus) -> int:
    """Deterministic bounded triage priority for any terminal status."""
    return PRIORITY_BY_SEVERITY[severity_for(status)]


def recommended_action_for(status: ReconciliationStatus) -> str | None:
    """Operator action for exceptions; ``None`` for MATCHED (nothing to do)."""
    if status is ReconciliationStatus.MATCHED:
        return None
    return ACTION_BY_EXCEPTION[status]


def annotate_triage(
    results: list[ReconciliationResult],
) -> list[ReconciliationResult]:
    """Return copies of ``results`` annotated with severity/priority/action.

    Pure annotation of engine output — statuses, amounts, reasons and
    ordering are untouched. Frozen models are copied, never mutated.
    """
    annotated: list[ReconciliationResult] = []
    for result in results:
        annotated.append(
            result.model_copy(
                update={
                    "severity": severity_for(result.status),
                    "priority": priority_for(result.status),
                    "recommended_action": recommended_action_for(
                        result.status
                    ),
                }
            )
        )
    return annotated


__all__ = [
    "ACTION_BY_EXCEPTION",
    "PRIORITY_BY_SEVERITY",
    "SEVERITY_BY_EXCEPTION",
    "annotate_triage",
    "priority_for",
    "recommended_action_for",
    "severity_for",
]

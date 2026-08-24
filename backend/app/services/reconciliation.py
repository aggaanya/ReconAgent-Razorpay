"""Deterministic reconciliation engine — Track 04 finance-ops loop.

Pure decision layer over typed domain records
(``app.schemas.reconciliation``): no database, no provider calls, and
**no LLM anywhere** — every classification below is a fixed rule an
auditor can re-run bit-for-bit. The LLM only ever explains the output.

Matching rules (evaluated in this exact order; first hit decides):

- **R0 — scope.** A payment with a known-but-settlement-ineligible status
  (``created``/``authorized``/``failed``) that also has no settlement
  lines is normal business (nothing to settle yet) and is out of scope.
  It is excluded, never counted as an exception.
- **R1 — INVALID_STATUS** (the STATUS_MISMATCH family). The payment's
  status is outside Razorpay's vocabulary, or the payment is not
  settlement-eligible yet settlement lines reference it. Either way the
  pair cannot be trusted as matched.
- **R2 — MISSING_SETTLEMENT.** An eligible payment has no settlement
  line. When processed refunds exist the reason says so explicitly
  (refunded money with no settlement needs review either way).
- **R3 — DUPLICATE_SETTLEMENT** (the DUPLICATE_TRANSACTION family). More
  than one line references the same payment. Amount comparison is
  meaningless while attribution is ambiguous, so duplicates take
  precedence over amount/currency checks.
- **R4 — CURRENCY_MISMATCH.** Line currency differs from the payment
  currency (checked before amount: a currency error makes any amount
  comparison meaningless).
- **R8 — REFUND_MISMATCH.** Processed refunds are integrity-checked
  before any settlement math: their sum may not exceed the payment
  amount, and each must share the payment's currency (no cross-currency
  subtraction). Pending/failed refunds moved nothing and never trigger
  this rule. A processed refund referencing an unknown payment becomes
  a MISSING_PAYMENT case anchored on the refund id (UNKNOWN_TRANSACTION
  family), reported alongside the other unattributable records.
- **R9 — SETTLEMENT_DELAY.** Only when ``max_settlement_delay_days`` is
  configured *and* both sides carry dates: a settlement that arrived
  more than the configured number of days after payment creation is
  late, even if its amount is perfect. Different days alone are normal
  business — only the exceeded window is an exception.
- **R5 — MATCHED / AMOUNT_MISMATCH / UNEXPLAINED_SETTLEMENT_DIFFERENCE.**
  The engine computes an **expected settlement** from recorded
  components::

      expected = gross − Σ(processed refunds) − fee − tax

  using only values present on the records (fee/tax come from the
  payment entity, never re-derived; absent components contribute zero).
  Equal minor-unit amounts match. A difference is AMOUNT_MISMATCH when
  no financial component was available at all (legacy shape — identical
  to the historical behavior), and UNEXPLAINED_SETTLEMENT_DIFFERENCE
  once fee/tax/refund information was actually accounted for.
- **R6 — MISSING_PAYMENT.** A settlement line references a payment id
  that does not exist in the batch (UNKNOWN_TRANSACTION family).
- **R7 — UNRESOLVED.** A line carries no usable attribution reference.
  No safe deterministic verdict exists — the engine reports the case for
  human review instead of guessing.

Determinism contract: results are ordered by input order (payments
first, then unattributable settlement lines, then orphaned processed
refunds); identical inputs always produce identical outputs. Inputs are
frozen Pydantic models — nothing is mutated. Duplicate ids on either
side are rejected outright (ambiguous data must not be silently
reconciled). Passing no refunds and leaving fee/tax/date fields unset
reproduces the legacy two-record behavior bit-for-bit.

Metrics (:func:`summarize`) are plain arithmetic over the results:
match rate is ``matched / total * 100`` and follows spec Part 6.4 — a
zero denominator yields ``None``, never a fabricated ``0%`` or ``100%``.
Ground-truth accuracy lives in :func:`evaluate_ground_truth`, which is
an *evaluation* utility for tests/benchmarks: ground truth is never an
input to :func:`reconcile`.
"""

import logging
import time

from app.schemas.reconciliation import (
    EXCEPTION_STATUSES,
    KNOWN_PAYMENT_STATUSES,
    REFUND_STATUS_PROCESSED,
    SETTLEMENT_ELIGIBLE_STATUSES,
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    EvaluationDataset,
    GroundTruthEntry,
    GroundTruthEvaluation,
    ExceptionSummary,
    ReconciliationPayment,
    ReconciliationRefund,
    ReconciliationReport,
    ReconciliationResult,
    ReconciliationSettlementLine,
    ReconciliationStatus,
    ReconciliationSummary,
    ReconciliationEvaluationReport,
)
from app.services.reconciliation_evidence import attach_evidence
from app.services.reconciliation_policy import annotate_triage
from app.services.reconciliation_synthetic import (
    DEFAULT_SEED,
    DEFAULT_SIZE,
    generate_synthetic_batch,
)

logger = logging.getLogger(__name__)

# Display precision for percentages — ratios, never money.
RATE_DECIMALS = 2


def _validate_unique_ids(
    payments: list[ReconciliationPayment],
    settlements: list[ReconciliationSettlementLine],
    refunds: list[ReconciliationRefund],
) -> None:
    """Reject ambiguous batches up front (fail loudly, reconcile cleanly)."""
    seen_payments: set[str] = set()
    for payment in payments:
        if payment.payment_id in seen_payments:
            raise ValueError(f"duplicate payment id {payment.payment_id!r}")
        seen_payments.add(payment.payment_id)
    seen_settlements: set[str] = set()
    for line in settlements:
        if line.settlement_id in seen_settlements:
            raise ValueError(
                f"duplicate settlement id {line.settlement_id!r}"
            )
        seen_settlements.add(line.settlement_id)
    seen_refunds: set[str] = set()
    for refund in refunds:
        if refund.refund_id in seen_refunds:
            raise ValueError(f"duplicate refund id {refund.refund_id!r}")
        seen_refunds.add(refund.refund_id)


def _components(
    *,
    gross: int,
    refunded_total: int | None,
    fee: int | None,
    tax: int | None,
) -> dict[str, int | None]:
    """Component breakdown attached to results for LLM explanation."""
    return {
        "gross_amount_minor": gross,
        "refunded_total_minor": refunded_total,
        "fee_minor": fee,
        "tax_minor": tax,
    }


def _line_result(
    *,
    source_id: str,
    line: ReconciliationSettlementLine,
    status: ReconciliationStatus,
    reason: str,
) -> ReconciliationResult:
    """Result anchored on a settlement line whose payment side is absent."""
    return ReconciliationResult(
        source_transaction_id=source_id,
        matched_transaction_id=None,
        status=status,
        expected_amount_minor=None,
        actual_amount_minor=line.amount_minor,
        difference_minor=None,
        currency=line.currency,
        reason=reason,
        exception_type=(
            status if status is not ReconciliationStatus.MATCHED else None
        ),
    )


def reconcile(
    payments: list[ReconciliationPayment],
    settlements: list[ReconciliationSettlementLine],
    refunds: list[ReconciliationRefund] | None = None,
    *,
    max_settlement_delay_days: int | None = None,
) -> list[ReconciliationResult]:
    """Classify every case deterministically (rules R0-R9, R5 evolved).

    ``refunds`` and ``max_settlement_delay_days`` are optional; omitting
    them reproduces the legacy two-record behavior exactly.
    """
    refunds = list(refunds or [])
    _validate_unique_ids(payments, settlements, refunds)

    lines_by_payment: dict[str, list[ReconciliationSettlementLine]] = {}
    unattributable: list[tuple[str, ReconciliationSettlementLine]] = []
    known_payment_ids = {p.payment_id for p in payments}
    for line in settlements:
        reference = (line.payment_id or "").strip()
        if not reference:
            unattributable.append((line.settlement_id, line))  # R7 later
        elif reference not in known_payment_ids:
            unattributable.append((reference, line))  # R6 later
        else:
            lines_by_payment.setdefault(reference, []).append(line)

    # Refunds split into money-moving (processed) vs inert, then grouped.
    processed_by_payment: dict[str, list[ReconciliationRefund]] = {}
    orphaned_refunds: list[tuple[str | None, ReconciliationRefund]] = []
    for refund in refunds:
        if refund.status != REFUND_STATUS_PROCESSED:
            continue  # pending/failed moved nothing — inert by definition
        reference = (refund.payment_id or "").strip()
        if not reference or reference not in known_payment_ids:
            orphaned_refunds.append((reference or None, refund))
        else:
            processed_by_payment.setdefault(reference, []).append(refund)

    results: list[ReconciliationResult] = []
    excluded_scope = 0
    for payment in payments:
        lines = lines_by_payment.get(payment.payment_id, [])
        payment_refunds = processed_by_payment.get(payment.payment_id, [])
        refunded_total = sum(r.amount_minor for r in payment_refunds)
        components = _components(
            gross=payment.amount_minor,
            refunded_total=refunded_total or None,
            fee=payment.fee_minor,
            tax=payment.tax_minor,
        )

        if payment.status not in KNOWN_PAYMENT_STATUSES:
            results.append(  # R1a — unknown vocabulary
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=(
                        lines[0].settlement_id if lines else None
                    ),
                    status=ReconciliationStatus.INVALID_STATUS,
                    expected_amount_minor=payment.amount_minor,
                    actual_amount_minor=(
                        lines[0].amount_minor if lines else None
                    ),
                    currency=payment.currency,
                    reason=(
                        f"Payment status {payment.status!r} is outside the "
                        "known Razorpay status vocabulary."
                    ),
                    exception_type=ReconciliationStatus.INVALID_STATUS,
                    **components,
                )
            )
            continue

        if payment.status not in SETTLEMENT_ELIGIBLE_STATUSES:
            if not lines:
                excluded_scope += 1  # R0 — nothing to settle yet
                continue
            results.append(  # R1b — settled but ineligible
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=lines[0].settlement_id,
                    status=ReconciliationStatus.INVALID_STATUS,
                    expected_amount_minor=payment.amount_minor,
                    actual_amount_minor=lines[0].amount_minor,
                    currency=payment.currency,
                    reason=(
                        f"Payment status {payment.status!r} is not "
                        "settlement-eligible, yet a settlement line "
                        "references it."
                    ),
                    exception_type=ReconciliationStatus.INVALID_STATUS,
                    **components,
                )
            )
            continue

        if not lines:  # R2
            suffix = (
                f" {len(payment_refunds)} processed refund(s) totaling "
                f"{refunded_total} minor units were recorded against it."
                if payment_refunds
                else ""
            )
            results.append(
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=None,
                    status=ReconciliationStatus.MISSING_SETTLEMENT,
                    expected_amount_minor=payment.amount_minor,
                    actual_amount_minor=None,
                    currency=payment.currency,
                    reason=(
                        "No settlement line references this "
                        "settlement-eligible payment." + suffix
                    ),
                    exception_type=ReconciliationStatus.MISSING_SETTLEMENT,
                    **components,
                )
            )
            continue

        if len(lines) > 1:  # R3 — attribution ambiguous
            line_ids = ", ".join(line.settlement_id for line in lines)
            results.append(
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=lines[0].settlement_id,
                    status=ReconciliationStatus.DUPLICATE_SETTLEMENT,
                    expected_amount_minor=payment.amount_minor,
                    actual_amount_minor=sum(
                        line.amount_minor for line in lines
                    ),
                    currency=payment.currency,
                    reason=(
                        f"{len(lines)} settlement lines reference this "
                        f"payment ({line_ids}); attribution is ambiguous, "
                        "so no amount comparison was attempted."
                    ),
                    exception_type=ReconciliationStatus.DUPLICATE_SETTLEMENT,
                    **components,
                )
            )
            continue

        line = lines[0]
        if line.currency != payment.currency:  # R4
            results.append(
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=line.settlement_id,
                    status=ReconciliationStatus.CURRENCY_MISMATCH,
                    expected_amount_minor=payment.amount_minor,
                    actual_amount_minor=line.amount_minor,
                    currency=payment.currency,
                    reason=(
                        f"Settled in {line.currency} but the payment was "
                        f"made in {payment.currency}; amounts were not "
                        "compared across currencies."
                    ),
                    exception_type=ReconciliationStatus.CURRENCY_MISMATCH,
                    **components,
                )
            )
            continue

        # R8 — refund integrity is validated BEFORE any settlement
        # comparison, so anomalies surface regardless of how the settled
        # amount compares: processed refunds must share the payment's
        # currency (no cross-currency subtraction) and may never total
        # more than the payment itself.
        anomaly = ""
        bad_currency = [
            r.refund_id
            for r in payment_refunds
            if r.currency != payment.currency
        ]
        if bad_currency:
            anomaly = (
                f"Processed refund(s) {', '.join(bad_currency)} are in "
                f"a different currency than the {payment.currency} "
                "payment; no cross-currency settlement math was "
                "attempted."
            )
        elif refunded_total > payment.amount_minor:
            anomaly = (
                f"Processed refunds total {refunded_total} minor "
                f"units, exceeding the payment amount of "
                f"{payment.amount_minor} minor units."
            )
        if anomaly:
            # Integrity violation — like R3, no valid amount
            # comparison happened, so difference stays unset.
            results.append(
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=line.settlement_id,
                    status=ReconciliationStatus.REFUND_MISMATCH,
                    expected_amount_minor=payment.amount_minor,
                    actual_amount_minor=line.amount_minor,
                    difference_minor=None,
                    currency=payment.currency,
                    reason=anomaly,
                    exception_type=ReconciliationStatus.REFUND_MISMATCH,
                    **components,
                )
            )
            continue

        # Expected settlement = gross − Σ(processed refunds) − fee − tax,
        # using only values recorded on the batch (never re-derived).
        expected_settlement = (
            payment.amount_minor - refunded_total
            - (payment.fee_minor or 0) - (payment.tax_minor or 0)
        )
        components_accounted = bool(payment_refunds) or (
            payment.fee_minor is not None or payment.tax_minor is not None
        )

        # R9 — the settlement-delay window is evaluated exactly once for
        # every single-line case. Precedence note: financial correctness
        # outranks timeliness, so SETTLEMENT_DELAY is primary only when
        # the money itself is exact; on a financial mismatch it demotes
        # to ``secondary_issues`` instead of replacing the verdict.
        delay_days = None
        if (
            max_settlement_delay_days is not None
            and payment.created_on is not None
            and line.settled_on is not None
        ):
            delay_days = (line.settled_on - payment.created_on).days
        delay_exceeded = bool(
            delay_days is not None
            and max_settlement_delay_days is not None
            and delay_days > max_settlement_delay_days
        )

        if line.amount_minor == expected_settlement:  # R5a — sole branch
            if delay_exceeded:  # exact money, late arrival → R9 primary
                results.append(
                    ReconciliationResult(
                        source_transaction_id=payment.payment_id,
                        matched_transaction_id=line.settlement_id,
                        status=ReconciliationStatus.SETTLEMENT_DELAY,
                        expected_amount_minor=expected_settlement,
                        actual_amount_minor=line.amount_minor,
                        difference_minor=0,
                        currency=payment.currency,
                        reason=(
                            f"Settled {delay_days} day(s) after payment "
                            f"creation, beyond the configured tolerance of "
                            f"{max_settlement_delay_days} day(s)."
                        ),
                        exception_type=ReconciliationStatus.SETTLEMENT_DELAY,
                        secondary_issues=(),
                        **components,
                    )
                )
                continue
            matched_reason = (
                "Settlement line matches the payment id, currency, and "
                "exact minor-unit amount."
            )
            if components_accounted:
                matched_reason = (
                    "Settlement equals the expected net amount "
                    f"({payment.amount_minor} gross − {refunded_total} "
                    f"refunded − {payment.fee_minor or 0} fee − "
                    f"{payment.tax_minor or 0} tax)."
                )
            results.append(
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=line.settlement_id,
                    status=ReconciliationStatus.MATCHED,
                    expected_amount_minor=expected_settlement,
                    actual_amount_minor=line.amount_minor,
                    difference_minor=0,
                    currency=payment.currency,
                    reason=matched_reason,
                    exception_type=None,
                    **components,
                )
            )
        else:  # R5b — residual difference after known components
            difference = line.amount_minor - expected_settlement
            mismatch_status = (
                ReconciliationStatus.AMOUNT_MISMATCH
                if not components_accounted
                else ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
            )
            if components_accounted:
                reason_text = (
                    f"Settlement differs from the expected net amount "
                    f"(gross {payment.amount_minor} − refunds "
                    f"{refunded_total} − fee {payment.fee_minor or 0} − "
                    f"tax {payment.tax_minor or 0} = {expected_settlement}) "
                    f"by {difference} minor units with no recorded "
                    "component to explain it."
                )
            else:
                reason_text = (
                    f"Settlement amount differs from the payment by "
                    f"{difference} minor units."
                )
            results.append(
                ReconciliationResult(
                    source_transaction_id=payment.payment_id,
                    matched_transaction_id=line.settlement_id,
                    status=mismatch_status,
                    expected_amount_minor=expected_settlement,
                    actual_amount_minor=line.amount_minor,
                    difference_minor=difference,
                    currency=payment.currency,
                    reason=reason_text,
                    exception_type=mismatch_status,
                    secondary_issues=(
                        (ReconciliationStatus.SETTLEMENT_DELAY,)
                        if delay_exceeded
                        else ()
                    ),
                    **components,
                )
            )

    # Lines whose payment side does not exist (R6) or is unusable (R7).
    for anchor, line in unattributable:
        if anchor == line.settlement_id:  # blank/missing reference → R7
            results.append(
                _line_result(
                    source_id=line.settlement_id,
                    line=line,
                    status=ReconciliationStatus.UNRESOLVED,
                    reason=(
                        "The settlement line has no usable payment "
                        "attribution reference; no safe deterministic "
                        "classification exists."
                    ),
                )
            )
        else:  # R6
            results.append(
                _line_result(
                    source_id=anchor,
                    line=line,
                    status=ReconciliationStatus.MISSING_PAYMENT,
                    reason=(
                        f"The attributed payment {anchor!r} does not exist "
                        "in the reconciled batch."
                    ),
                )
            )

    # Orphaned processed refunds: money left the merchant account for a
    # payment the batch never saw. Blank references stay UNRESOLVED —
    # no safe deterministic verdict exists for them either.
    for anchor, refund in orphaned_refunds:
        if anchor is None:
            results.append(
                ReconciliationResult(
                    source_transaction_id=refund.refund_id,
                    matched_transaction_id=None,
                    status=ReconciliationStatus.UNRESOLVED,
                    expected_amount_minor=None,
                    actual_amount_minor=refund.amount_minor,
                    difference_minor=None,
                    currency=refund.currency,
                    reason=(
                        "The processed refund has no usable payment "
                        "attribution reference; no safe deterministic "
                        "classification exists."
                    ),
                    exception_type=ReconciliationStatus.UNRESOLVED,
                )
            )
        else:
            results.append(
                ReconciliationResult(
                    source_transaction_id=refund.refund_id,
                    matched_transaction_id=None,
                    status=ReconciliationStatus.MISSING_PAYMENT,
                    expected_amount_minor=None,
                    actual_amount_minor=refund.amount_minor,
                    difference_minor=None,
                    currency=refund.currency,
                    reason=(
                        f"The parent payment {anchor!r} of this processed "
                        "refund does not exist in the reconciled batch."
                    ),
                    exception_type=ReconciliationStatus.MISSING_PAYMENT,
                )
            )

    if excluded_scope:
        logger.info(
            "Reconciliation excluded %d payment(s) from scope (ineligible "
            "status with no settlement lines)",
            excluded_scope,
        )
    logger.info(
        "Reconciliation produced %d result(s) (%d exception(s))",
        len(results),
        sum(1 for r in results if r.is_exception),
    )
    return results


def summarize(
    results: list[ReconciliationResult],
    *,
    processing_seconds: float | None = None,
) -> ReconciliationSummary:
    """Batch metrics — pure arithmetic over decisions (never LLM-computed)."""
    total = len(results)
    matched = sum(
        1 for r in results if r.status is ReconciliationStatus.MATCHED
    )
    exceptions = [r for r in results if r.is_exception]
    breakdown: dict[str, int] = {}
    for status in sorted(EXCEPTION_STATUSES, key=lambda s: s.value):
        count = sum(1 for r in exceptions if r.status is status)
        if count:
            breakdown[status.value] = count

    seconds = max(processing_seconds or 0.0, 0.0)
    return ReconciliationSummary(
        total_records=total,
        matched_count=matched,
        exception_count=len(exceptions),
        unresolved_count=sum(
            1
            for r in results
            if r.status is ReconciliationStatus.UNRESOLVED
        ),
        match_rate=(
            round(matched / total * 100, RATE_DECIMALS) if total else None
        ),
        processing_time_ms=round(seconds * 1000, 3),
        throughput_records_per_second=(
            round(total / seconds, RATE_DECIMALS) if seconds > 0 else 0.0
        ),
        exception_breakdown=breakdown,
    )


def build_exception_summary(
    results: list[ReconciliationResult],
) -> ExceptionSummary:
    """Deterministic aggregate metrics for exceptions (exposure, priorities, top items)."""
    exceptions = [r for r in results if r.is_exception]
    total_exceptions = len(exceptions)
    total_exposure = sum(r.financial_impact_minor or 0 for r in exceptions)

    crit_cnt = sum(1 for r in exceptions if r.severity == "CRITICAL")
    high_cnt = sum(1 for r in exceptions if r.severity == "HIGH")
    med_cnt = sum(1 for r in exceptions if r.severity == "MEDIUM")
    low_cnt = sum(1 for r in exceptions if r.severity == "LOW")

    crit_exp = sum(r.financial_impact_minor or 0 for r in exceptions if r.severity == "CRITICAL")
    high_exp = sum(r.financial_impact_minor or 0 for r in exceptions if r.severity == "HIGH")
    med_exp = sum(r.financial_impact_minor or 0 for r in exceptions if r.severity == "MEDIUM")
    low_exp = sum(r.financial_impact_minor or 0 for r in exceptions if r.severity == "LOW")

    cat_map: dict[str, dict] = {}
    for r in exceptions:
        cat = r.status.value
        exp = r.financial_impact_minor or 0
        if cat not in cat_map:
            cat_map[cat] = {"category": cat, "count": 0, "exposure_minor": 0}
        cat_map[cat]["count"] += 1
        cat_map[cat]["exposure_minor"] += exp

    top_categories = sorted(cat_map.values(), key=lambda c: (c["exposure_minor"], c["count"]), reverse=True)

    sorted_by_impact = sorted(exceptions, key=lambda r: (r.financial_impact_minor or 0, r.priority or 0), reverse=True)
    top_impact = [
        {
            "source_transaction_id": r.source_transaction_id,
            "exception_type": r.status.value,
            "financial_impact_minor": r.financial_impact_minor or 0,
            "severity": r.severity,
            "priority": r.priority,
        }
        for r in sorted_by_impact[:5]
    ]

    unresolved_exp = max([r.financial_impact_minor or 0 for r in exceptions if r.status is ReconciliationStatus.UNRESOLVED], default=0)

    return ExceptionSummary(
        total_exceptions=total_exceptions,
        total_financial_exposure_minor=total_exposure,
        exposure_currency="INR",
        critical_count=crit_cnt,
        high_count=high_cnt,
        medium_count=med_cnt,
        low_count=low_cnt,
        critical_exposure_minor=crit_exp,
        high_exposure_minor=high_exp,
        medium_exposure_minor=med_exp,
        low_exposure_minor=low_exp,
        top_exception_categories=top_categories,
        top_impact_exceptions=top_impact,
        largest_unresolved_exposure_minor=unresolved_exp,
    )


def build_report(
    payments: list[ReconciliationPayment],
    settlements: list[ReconciliationSettlementLine],
    refunds: list[ReconciliationRefund] | None = None,
    *,
    max_settlement_delay_days: int | None = None,
) -> ReconciliationReport:
    """One-shot run: reconcile, time it, summarize, split exceptions.

    Decisions, ordering and arithmetic come solely from
    :func:`reconcile`; the triage and evidence passes afterwards are pure
    additive annotations (copies via ``model_copy``, never mutation) that
    rank for operators and attach the structured audit view respectively.
    """
    started = time.perf_counter()
    results = annotate_triage(
        reconcile(
            payments,
            settlements,
            refunds,
            max_settlement_delay_days=max_settlement_delay_days,
        )
    )
    results = attach_evidence(results, payments, settlements, refunds)
    elapsed = time.perf_counter() - started
    return ReconciliationReport(
        summary=summarize(results, processing_seconds=elapsed),
        exception_summary=build_exception_summary(results),
        results=results,
        exceptions=[r for r in results if r.is_exception],
    )


def evaluate_ground_truth(
    results: list[ReconciliationResult],
    ground_truth: list[GroundTruthEntry],
) -> GroundTruthEvaluation:
    """Compare engine decisions against expected outcomes (evaluation only).

    Alignment key is each entry's ``case_id`` matching the result's
    ``source_transaction_id`` — a generator-side guarantee, so this
    function stays a dumb comparator with zero domain knowledge. It is
    called by tests/benchmarks only; the engine never sees ground truth.

    Checks, per case: terminal status (strict), then — only when the
    truth pins them — expected/actual amounts (exact minor units) and a
    reason fragment that must appear in the engine's reason string.
    Compound findings are verified too: ``secondary_issues`` must equal
    ``expected_secondary_issues`` exactly (an empty truth expectation
    demands no secondary issues; a mismatch in either direction fails).
    Detection metrics treat "is an exception" as the positive class:
    false positives are truth-matched cases the engine flagged, false
    negatives are truth-exceptions the engine matched. Precision/recall/
    F1 follow the zero-denominator-None convention (spec Part 6.4).
    """
    by_source = {result.source_transaction_id: result for result in results}
    correct = 0
    failed_cases: set[str] = set()
    mismatches: list[dict[str, str]] = []

    def _flag(case_id: str, kind: str, expected: str, actual: str) -> None:
        failed_cases.add(case_id)
        mismatches.append(
            {
                "case_id": case_id,
                "kind": kind,
                "expected": expected,
                "actual": actual,
            }
        )

    tp = fp = fn = 0
    missing_ids: set[str] = set()
    for entry in ground_truth:
        result = by_source.get(entry.case_id)
        if result is None:
            _flag(entry.case_id, "missing", entry.expected_status.value,
                  "<missing>")
            missing_ids.add(entry.case_id)
            if entry.expected_status is not ReconciliationStatus.MATCHED:
                fn += 1  # a truth-exception the engine never reported
            continue
        engine_exception = result.is_exception
        truth_exception = entry.expected_status is not (
            ReconciliationStatus.MATCHED
        )
        if truth_exception and engine_exception:
            tp += 1
        elif truth_exception:
            fn += 1
        elif engine_exception:
            fp += 1

        ok = True
        if result.status is not entry.expected_status:
            ok = False
            _flag(
                entry.case_id,
                "status",
                entry.expected_status.value,
                result.status.value,
            )
        if (
            entry.expected_amount_minor is not None
            and result.expected_amount_minor != entry.expected_amount_minor
        ):
            ok = False
            _flag(
                entry.case_id,
                "expected_amount",
                str(entry.expected_amount_minor),
                str(result.expected_amount_minor),
            )
        if (
            entry.actual_amount_minor is not None
            and result.actual_amount_minor != entry.actual_amount_minor
        ):
            ok = False
            _flag(
                entry.case_id,
                "actual_amount",
                str(entry.actual_amount_minor),
                str(result.actual_amount_minor),
            )
        if (
            entry.expected_reason_fragment is not None
            and entry.expected_reason_fragment not in result.reason
        ):
            ok = False
            _flag(
                entry.case_id,
                "reason",
                entry.expected_reason_fragment,
                result.reason,
            )
        if result.secondary_issues != entry.expected_secondary_issues:
            ok = False
            _flag(
                entry.case_id,
                "secondary_issues",
                str(
                    tuple(status.value for status in
                          entry.expected_secondary_issues)
                ),
                str(tuple(status.value for status in result.secondary_issues)),
            )
        if ok:
            correct += 1

    total = len(ground_truth)
    incorrect = len(failed_cases - missing_ids)
    precision_den = tp + fp
    recall_den = tp + fn
    precision = round(tp / precision_den * 100, RATE_DECIMALS) if precision_den else None
    recall = round(tp / recall_den * 100, RATE_DECIMALS) if recall_den else None
    f1 = (
        round(2 * precision * recall / (precision + recall), RATE_DECIMALS)
        if precision is not None and recall is not None
        and (precision + recall) > 0
        else None
    )
    return GroundTruthEvaluation(
        total_cases=total,
        correct_decisions=correct,
        incorrect_decisions=incorrect,
        accuracy=(
            round(correct / total * 100, RATE_DECIMALS) if total else None
        ),
        mismatches=mismatches,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def evaluate_batch_with_ground_truth(
    seed: int = DEFAULT_SEED,
    size: int = DEFAULT_SIZE,
    max_settlement_delay_days: int = DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
) -> ReconciliationEvaluationReport:
    """Run the seeded batch through engine AND evaluator (evaluation only).

    This is the shared core of the two evaluation surfaces — the
    benchmark CLI (``scripts/reconcile_benchmark.py --json``) and the
    read-only HTTP surface ``GET /api/v1/ai/reconcile/evaluation``. The
    serving endpoint never calls this: ground truth exists only here, in
    tests, and in the generator.

    Returns aggregate measured metrics plus mismatch case ids/kinds;
    raw ground-truth records are never exposed.
    """
    batch = generate_synthetic_batch(seed=seed, size=size)
    report = build_report(
        batch.payments,
        batch.settlements,
        batch.refunds,
        max_settlement_delay_days=max_settlement_delay_days,
    )
    evaluation = evaluate_ground_truth(report.results, batch.ground_truth)
    summary = report.summary
    return ReconciliationEvaluationReport(
        dataset=EvaluationDataset(source="synthetic", seed=seed, size=size),
        total_records=summary.total_records,
        matched_count=summary.matched_count,
        exception_count=summary.exception_count,
        unresolved_count=summary.unresolved_count,
        match_rate=summary.match_rate,
        accuracy=evaluation.accuracy,
        precision=evaluation.precision,
        recall=evaluation.recall,
        f1=evaluation.f1,
        true_positives=evaluation.true_positives,
        false_positives=evaluation.false_positives,
        false_negatives=evaluation.false_negatives,
        throughput_records_per_second=(
            summary.throughput_records_per_second
        ),
        exception_breakdown=summary.exception_breakdown,
        mismatches=evaluation.mismatches,
    )


__all__ = [
    "build_report",
    "evaluate_batch_with_ground_truth",
    "evaluate_ground_truth",
    "reconcile",
    "summarize",
]

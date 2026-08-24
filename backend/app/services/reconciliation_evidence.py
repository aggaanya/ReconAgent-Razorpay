"""Structured reconciliation evidence — a pure projection layer.

Attaches :class:`app.schemas.reconciliation.ReconciliationEvidence` to
each engine result by **copying** values that already exist on the
frozen inputs and the decision itself. This module never classifies,
never computes money (the only sums it touches are the ones the engine
already wrote onto the result) and never mutates anything: results are
copied via ``model_copy`` exactly like the triage annotation pass.

Layer position: sits beside ``app.services.reconciliation_policy`` as an
additive enrichment over engine output. The deterministic engine
(``reconcile``) stays untouched; ``build_report`` applies this pass so
every report surface (Finance Tool envelope, HTTP response) carries
evidence. Raw ``reconcile()`` output keeps ``evidence=None``.

Attribution recap (mirrors the engine's grouping rules read-only):

- Payment-anchored results: ``source_transaction_id`` is the payment id;
  settlement lines and processed refunds are grouped by their payment
  reference in input order.
- Settlement lines whose payment reference is unknown/blank anchor on
  the phantom payment id (R6) or the line's own settlement id (R7).
- Orphaned processed refunds anchor on the refund id.

Lookup precedence payment → refund → settlement line mirrors the
engine's processing order, so pathological cross-collection id
collisions resolve the same way the engine resolved them.
"""

from app.schemas.reconciliation import (
    REFUND_STATUS_PROCESSED,
    ReconciliationEvidence,
    RefundEvidence,
    ReconciliationPayment,
    ReconciliationRefund,
    ReconciliationResult,
    ReconciliationSettlementLine,
)


def _rules_triggered(result: ReconciliationResult) -> tuple[str, ...]:
    """Terminal status value plus any secondary issue values, in order."""
    return (
        result.status.value,
        *(issue.value for issue in result.secondary_issues),
    )


def _refund_rows(
    refunds: list[ReconciliationRefund],
) -> tuple[RefundEvidence, ...]:
    return tuple(
        RefundEvidence(
            refund_id=refund.refund_id,
            amount_minor=refund.amount_minor,
            currency=refund.currency,
            status=refund.status,
        )
        for refund in refunds
    )


def attach_evidence(
    results: list[ReconciliationResult],
    payments: list[ReconciliationPayment],
    settlements: list[ReconciliationSettlementLine],
    refunds: list[ReconciliationRefund] | None = None,
) -> list[ReconciliationResult]:
    """Return copies of ``results`` with ``evidence`` attached.

    Pure and deterministic: identical inputs yield identical outputs,
    input order is preserved everywhere, and no field of any record is
    recomputed or altered.
    """
    payments_by_id = {p.payment_id: p for p in payments}
    lines_by_id = {line.settlement_id: line for line in settlements}
    lines_by_payment: dict[str, list[ReconciliationSettlementLine]] = {}
    for line in settlements:
        reference = (line.payment_id or "").strip()
        if reference:
            lines_by_payment.setdefault(reference, []).append(line)

    # Only processed refunds debit settlements (engine rule R8); inert
    # refunds are listed nowhere because no result is anchored on them.
    processed_by_id: dict[str, ReconciliationRefund] = {}
    processed_by_payment: dict[str, list[ReconciliationRefund]] = {}
    for refund in refunds or []:
        if refund.status != REFUND_STATUS_PROCESSED:
            continue
        processed_by_id[refund.refund_id] = refund
        reference = (refund.payment_id or "").strip()
        if reference:
            processed_by_payment.setdefault(reference, []).append(refund)

    annotated: list[ReconciliationResult] = []
    for result in results:
        source = result.source_transaction_id
        payment = payments_by_id.get(source)
        orphan_refund = (
            processed_by_id.get(source) if payment is None else None
        )

        if payment is not None:
            pay_lines = lines_by_payment.get(source, [])
            primary = pay_lines[0] if pay_lines else None
            evidence = ReconciliationEvidence(
                payment_id=payment.payment_id,
                payment_amount_minor=payment.amount_minor,
                settlement_id=(
                    primary.settlement_id if primary is not None else None
                ),
                settlement_amount_minor=(
                    primary.amount_minor if primary is not None else None
                ),
                additional_settlement_ids=tuple(
                    line.settlement_id for line in pay_lines[1:]
                ),
                refunds=_refund_rows(
                    processed_by_payment.get(source, [])
                ),
                gross_amount_minor=result.gross_amount_minor,
                refunded_total_minor=result.refunded_total_minor,
                fee_minor=result.fee_minor,
                tax_minor=result.tax_minor,
                expected_settlement_minor=result.expected_amount_minor,
                actual_settlement_minor=result.actual_amount_minor,
                difference_minor=result.difference_minor,
                status=result.status,
                rules_triggered=_rules_triggered(result),
                reason=result.reason,
                financial_impact_minor=result.financial_impact_minor,
                financial_impact_reason=result.financial_impact_reason,
            )
        elif orphan_refund is not None:
            # Money moved against a payment this batch never saw: the
            # refund itself is the only financial fact (a refund is not
            # a settlement, so the settlement side stays empty).
            evidence = ReconciliationEvidence(
                payment_id=orphan_refund.payment_id,
                refunds=(
                    RefundEvidence(
                        refund_id=orphan_refund.refund_id,
                        amount_minor=orphan_refund.amount_minor,
                        currency=orphan_refund.currency,
                        status=orphan_refund.status,
                    ),
                ),
                expected_settlement_minor=result.expected_amount_minor,
                difference_minor=result.difference_minor,
                status=result.status,
                rules_triggered=_rules_triggered(result),
                reason=result.reason,
                financial_impact_minor=result.financial_impact_minor,
                financial_impact_reason=result.financial_impact_reason,
            )
        else:
            # Settlement-line-anchored case (unknown or blank payment
            # attribution): the line is real, the payment side is not.
            # A missing-payment anchor is the phantom payment reference,
            # so grouped lookup comes first; blank references anchor on
            # the settlement id itself.
            pay_lines = lines_by_payment.get(source, [])
            if not pay_lines:
                direct = lines_by_id.get(source)
                pay_lines = [direct] if direct is not None else []
            primary = pay_lines[0] if pay_lines else None
            evidence = ReconciliationEvidence(
                settlement_id=(
                    primary.settlement_id if primary is not None else None
                ),
                settlement_amount_minor=(
                    primary.amount_minor if primary is not None else None
                ),
                additional_settlement_ids=tuple(
                    item.settlement_id for item in pay_lines[1:]
                ),
                expected_settlement_minor=result.expected_amount_minor,
                actual_settlement_minor=result.actual_amount_minor,
                difference_minor=result.difference_minor,
                status=result.status,
                rules_triggered=_rules_triggered(result),
                reason=result.reason,
                financial_impact_minor=result.financial_impact_minor,
                financial_impact_reason=result.financial_impact_reason,
            )

        annotated.append(
            result.model_copy(update={"evidence": evidence})
        )
    return annotated


__all__ = ["attach_evidence"]

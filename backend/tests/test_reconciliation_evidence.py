"""Structured reconciliation evidence tests.

Pins Phase 1's deliverable: every report surface carries a structured
evidence view copied verbatim from the deterministic engine output. The
engine itself is untouched — these tests additionally guard that raw
``reconcile()`` results keep ``evidence=None`` (backward compatibility)
and that attaching evidence never changes any pre-existing field.

Covers: matched transaction, amount mismatch, fee/tax deduction,
refund, unexplained settlement difference, missing settlement, and
settlement delay. No LLM, no database.
"""

from datetime import date, timedelta

from app.schemas.reconciliation import (
    ReconciliationPayment,
    ReconciliationRefund,
    ReconciliationSettlementLine,
    ReconciliationStatus,
)
from app.services.reconciliation import build_report, reconcile


def pay(
    pid="pay_0001",
    amount_minor=100_000,
    currency="INR",
    status="captured",
    **extra,
):
    return ReconciliationPayment(
        payment_id=pid,
        amount_minor=amount_minor,
        currency=currency,
        status=status,
        **extra,
    )


def line(
    sid="set_0001",
    pid="pay_0001",
    amount_minor=100_000,
    currency="INR",
    **extra,
):
    return ReconciliationSettlementLine(
        settlement_id=sid,
        payment_id=pid,
        amount_minor=amount_minor,
        currency=currency,
        **extra,
    )


def refund(
    rid="rfnd_0001",
    pid="pay_0001",
    amount_minor=10_000,
    currency="INR",
    status="processed",
):
    return ReconciliationRefund(
        refund_id=rid,
        payment_id=pid,
        amount_minor=amount_minor,
        currency=currency,
        status=status,
    )


# --- the seven required scenarios --------------------------------------


class TestEvidenceScenarios:
    def test_matched_transaction_evidence(self):
        """A clean match exposes both sides plus the zero difference."""
        report = build_report([pay()], [line()])
        result = report.results[0]
        assert result.status is ReconciliationStatus.MATCHED
        ev = result.evidence
        assert ev is not None
        assert ev.payment_id == "pay_0001"
        assert ev.payment_amount_minor == 100_000
        assert ev.settlement_id == "set_0001"
        assert ev.settlement_amount_minor == 100_000
        assert ev.refunds == ()
        assert ev.expected_settlement_minor == 100_000
        assert ev.actual_settlement_minor == 100_000
        assert ev.difference_minor == 0
        assert ev.status is ReconciliationStatus.MATCHED
        assert ev.rules_triggered == ("MATCHED",)
        assert ev.reason == result.reason

    def test_amount_mismatch_evidence(self):
        """Legacy shape: difference copied exactly, no fabricated parts."""
        report = build_report(
            [pay(amount_minor=250_000)],
            [line(amount_minor=249_999)],
        )
        result = report.results[0]
        assert result.status is ReconciliationStatus.AMOUNT_MISMATCH
        ev = result.evidence
        assert ev.payment_id == "pay_0001"
        assert ev.settlement_id == "set_0001"
        assert ev.payment_amount_minor == 250_000
        assert ev.settlement_amount_minor == 249_999
        assert ev.gross_amount_minor == 250_000
        assert ev.fee_minor is None
        assert ev.tax_minor is None
        assert ev.refunded_total_minor is None
        assert ev.expected_settlement_minor == 250_000
        assert ev.actual_settlement_minor == 249_999
        assert ev.difference_minor == -1
        assert ev.rules_triggered == ("AMOUNT_MISMATCH",)

    def test_fee_and_tax_deduction_evidence(self):
        """gross 100000 − fee 2000 − tax 400 = 97600 expected & settled."""
        payment = pay(fee_minor=2_000, tax_minor=400)
        report = build_report(
            [payment], [line(amount_minor=97_600)]
        )
        result = report.results[0]
        assert result.status is ReconciliationStatus.MATCHED
        ev = result.evidence
        assert ev.gross_amount_minor == 100_000
        assert ev.fee_minor == 2_000
        assert ev.tax_minor == 400
        assert ev.refunded_total_minor is None
        # Values are the engine's own outputs, not recomputed here.
        assert result.expected_amount_minor == 97_600
        assert ev.expected_settlement_minor == 97_600
        assert ev.actual_settlement_minor == 97_600
        assert ev.difference_minor == 0

    def test_refund_evidence_itemizes_each_refund(self):
        """Two partial refunds appear individually with id and amount."""
        payment = pay(amount_minor=100_000)
        refunds = [
            refund(rid="rfnd_a", amount_minor=30_000),
            refund(rid="rfnd_b", amount_minor=20_000),
        ]
        report = build_report(
            [payment], [line(amount_minor=50_000)], refunds
        )
        result = report.results[0]
        assert result.status is ReconciliationStatus.MATCHED
        ev = result.evidence
        assert [(r.refund_id, r.amount_minor) for r in ev.refunds] == [
            ("rfnd_a", 30_000),
            ("rfnd_b", 20_000),
        ]
        assert all(r.currency == "INR" for r in ev.refunds)
        assert ev.refunded_total_minor == 50_000
        assert ev.settlement_id == "set_0001"
        assert ev.settlement_amount_minor == 50_000
        assert ev.expected_settlement_minor == 50_000
        assert ev.difference_minor == 0

    def test_unexplained_settlement_difference_evidence(self):
        """Residual difference after components, copied with its rule."""
        payment = pay(fee_minor=2_000, tax_minor=400)
        report = build_report(
            [payment], [line(amount_minor=97_550)]
        )
        result = report.results[0]
        assert (
            result.status
            is ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
        )
        ev = result.evidence
        assert ev.fee_minor == 2_000
        assert ev.tax_minor == 400
        assert ev.expected_settlement_minor == 97_600
        assert ev.actual_settlement_minor == 97_550
        assert ev.difference_minor == -50
        assert ev.rules_triggered == (
            "UNEXPLAINED_SETTLEMENT_DIFFERENCE",
        )

    def test_missing_settlement_evidence_has_no_settlement_side(self):
        report = build_report([pay()], [])
        result = report.results[0]
        assert result.status is ReconciliationStatus.MISSING_SETTLEMENT
        ev = result.evidence
        assert ev.payment_id == "pay_0001"
        assert ev.payment_amount_minor == 100_000
        assert ev.gross_amount_minor == 100_000
        assert ev.settlement_id is None
        assert ev.settlement_amount_minor is None
        assert ev.actual_settlement_minor is None
        assert ev.difference_minor is None
        assert ev.rules_triggered == ("MISSING_SETTLEMENT",)

    def test_settlement_delay_evidence_lists_the_timing_rule(self):
        created = date(2026, 3, 1)
        payment = pay(created_on=created)
        settlement_line = line(
            settled_on=created + timedelta(days=9), amount_minor=100_000
        )
        report = build_report(
            [payment],
            [settlement_line],
            max_settlement_delay_days=3,
        )
        result = report.results[0]
        assert result.status is ReconciliationStatus.SETTLEMENT_DELAY
        ev = result.evidence
        assert ev.difference_minor == 0  # money was exact
        assert ev.rules_triggered == ("SETTLEMENT_DELAY",)
        assert ev.reason == result.reason

    def test_secondary_issue_joins_rules_triggered(self):
        """A financial mismatch that was ALSO late lists both rules."""
        created = date(2026, 3, 1)
        payment = pay(fee_minor=2_000, tax_minor=400, created_on=created)
        settlement_line = line(
            amount_minor=97_550,
            settled_on=created + timedelta(days=9),
        )
        report = build_report(
            [payment],
            [settlement_line],
            max_settlement_delay_days=3,
        )
        result = report.results[0]
        assert (
            result.status
            is ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
        )
        assert result.secondary_issues == (
            ReconciliationStatus.SETTLEMENT_DELAY,
        )
        assert result.evidence.rules_triggered == (
            "UNEXPLAINED_SETTLEMENT_DIFFERENCE",
            "SETTLEMENT_DELAY",
        )

    def test_duplicate_and_missing_payment_evidence_shapes(self):
        """DUPLICATE_SETTLEMENT lists extra line ids; MISSING_PAYMENT on
        an orphan line anchors the settlement side only."""
        report = build_report(
            [pay()],
            [
                line(sid="set_a", amount_minor=100_000),
                line(sid="set_b", amount_minor=999),
                line(sid="set_orphan", pid="pay_ghost", amount_minor=500),
            ],
        )
        by_source = {r.source_transaction_id: r for r in report.results}
        dup = by_source["pay_0001"].evidence
        assert dup.settlement_id == "set_a"
        assert dup.additional_settlement_ids == ("set_b",)
        orphan = by_source["pay_ghost"].evidence
        assert orphan.payment_id is None
        assert orphan.settlement_id == "set_orphan"
        assert orphan.settlement_amount_minor == 500
        assert orphan.rules_triggered == ("MISSING_PAYMENT",)


# --- contract guards ----------------------------------------------------


class TestEvidenceContract:
    def test_raw_reconcile_output_keeps_evidence_none(self):
        """Backward compatibility: engine decisions carry no evidence;
        only the report pass attaches it."""
        results = reconcile([pay()], [line()])
        assert results[0].evidence is None

    def test_attaching_evidence_changes_no_existing_field(self):
        from app.services.reconciliation_policy import annotate_triage

        batch_payments = [
            pay("p_ok"),
            pay("p_amt", amount_minor=500),
            pay("p_miss"),
            pay("p_fee", fee_minor=10, tax_minor=1),
            pay("p_cur", currency="USD"),
        ]
        settlements = [
            line("s_ok", "p_ok"),
            line("s_amt", "p_amt", amount_minor=400),
            line("s_fee", "p_fee", amount_minor=489),
            line("s_cur", "p_cur", currency="INR"),
        ]
        refunds = [refund(pid="p_ok", amount_minor=25_000)]
        # Compare at the same pipeline stage: triage applied, evidence
        # not yet — the evidence pass must add ONLY ``evidence``.
        triaged = annotate_triage(
            reconcile(batch_payments, settlements, refunds)
        )
        attached = build_report(batch_payments, settlements, refunds).results
        assert len(triaged) == len(attached)
        for before, after in zip(triaged, attached):
            dumped = before.model_dump()
            enriched = after.model_dump()
            evidence = enriched.pop("evidence")
            assert evidence is not None
            assert dumped.pop("evidence") is None
            assert dumped == enriched

    def test_identical_inputs_yield_identical_evidence(self):
        payments = [pay(fee_minor=2_000, tax_minor=400)]
        lines = [line(amount_minor=97_600)]
        first = build_report(payments, lines)
        second = build_report(payments, lines)
        assert first.results[0].model_dump() == second.results[0].model_dump()

    def test_inert_refunds_never_enter_evidence(self):
        """Pending/failed refunds moved nothing — they are not evidence."""
        inert = [
            refund(rid="rfnd_p", status="pending"),
            refund(rid="rfnd_f", status="failed"),
        ]
        report = build_report([pay()], [line()], inert)
        ev = report.results[0].evidence
        assert ev.refunds == ()
        assert ev.refunded_total_minor is None

    def test_evidence_survives_json_round_trip_without_ground_truth_keys(
        self,
    ):
        import json

        report = build_report([pay()], [line(amount_minor=99_999)])
        encoded = json.dumps(report.model_dump(mode="json"))
        assert "ground_truth" not in encoded
        assert "expected_status" not in encoded
        assert "case_id" not in encoded

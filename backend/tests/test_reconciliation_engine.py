"""Deterministic reconciliation engine tests.

Pins every matching rule (R1-R7), the scope rule (R0), metric formulas,
zero-denominator semantics, determinism across repeated runs, input
immutability, and the ground-truth comparator. No LLM, no database —
the engine is pure and these tests keep it that way.
"""

import pytest
from pydantic import ValidationError

from app.schemas.reconciliation import (
    KNOWN_PAYMENT_STATUSES,
    SETTLEMENT_ELIGIBLE_STATUSES,
    GroundTruthEntry,
    ReconciliationPayment,
    ReconciliationResult,
    ReconciliationSettlementLine,
    ReconciliationStatus,
)
from app.services.reconciliation import (
    build_report,
    evaluate_ground_truth,
    reconcile,
    summarize,
)


def pay(
    pid="pay_0001",
    amount_minor=100_000,
    currency="INR",
    status="captured",
):
    return ReconciliationPayment(
        payment_id=pid,
        amount_minor=amount_minor,
        currency=currency,
        status=status,
    )


def line(
    sid="set_0001",
    pid="pay_0001",
    amount_minor=100_000,
    currency="INR",
):
    return ReconciliationSettlementLine(
        settlement_id=sid,
        payment_id=pid,
        amount_minor=amount_minor,
        currency=currency,
    )


def single(payment, *lines):
    return reconcile([payment], list(lines))


# --- rule coverage --------------------------------------------------------------


class TestMatchingRules:
    def test_exact_match(self):
        result = single(pay(), line())[0]
        assert result.status is ReconciliationStatus.MATCHED
        assert result.exception_type is None
        assert result.matched_transaction_id == "set_0001"
        assert result.expected_amount_minor == 100_000
        assert result.actual_amount_minor == 100_000
        assert result.difference_minor == 0
        assert result.is_exception is False

    def test_amount_mismatch_reports_exact_difference(self):
        result = single(pay(amount_minor=250_000), line(amount_minor=249_999))[0]
        assert result.status is ReconciliationStatus.AMOUNT_MISMATCH
        assert result.exception_type is ReconciliationStatus.AMOUNT_MISMATCH
        assert result.difference_minor == -1

    def test_missing_settlement(self):
        result = single(pay())[0]
        assert result.status is ReconciliationStatus.MISSING_SETTLEMENT
        assert result.matched_transaction_id is None
        assert result.actual_amount_minor is None

    def test_duplicate_settlement_takes_precedence_over_amount_check(self):
        result = single(
            pay(),
            line(sid="set_a", amount_minor=100_000),
            line(sid="set_b", amount_minor=999),  # even a bad amount
        )[0]
        assert result.status is ReconciliationStatus.DUPLICATE_SETTLEMENT
        assert result.actual_amount_minor == 100_000 + 999
        assert "set_a" in result.reason and "set_b" in result.reason

    def test_currency_mismatch_takes_precedence_over_amount_check(self):
        result = single(pay(), line(currency="USD", amount_minor=1))[0]
        assert result.status is ReconciliationStatus.CURRENCY_MISMATCH
        # Currency errors never compare amounts.
        assert result.difference_minor is None

    def test_invalid_status_unknown_vocabulary(self):
        result = single(pay(status="processing"), line())[0]
        assert result.status is ReconciliationStatus.INVALID_STATUS

    def test_invalid_status_ineligible_but_settled(self):
        result = single(pay(status="failed"), line())[0]
        assert result.status is ReconciliationStatus.INVALID_STATUS
        assert "not settlement-eligible" in result.reason

    def test_ineligible_status_without_settlement_is_out_of_scope(self):
        results = reconcile(
            [pay(status="created", pid="pay_ok"),
             pay(pid="pay_captured")],
            [line(pid="pay_captured")],
        )
        sources = [r.source_transaction_id for r in results]
        assert sources == ["pay_captured"]  # created payment excluded silently
        assert results[0].status is ReconciliationStatus.MATCHED

    def test_missing_payment(self):
        orphan = line(sid="set_orphan", pid="pay_ghost")
        result = reconcile([], [orphan])[0]
        assert result.status is ReconciliationStatus.MISSING_PAYMENT
        assert result.source_transaction_id == "pay_ghost"
        assert result.actual_amount_minor == orphan.amount_minor

    def test_unresolved_blank_attribution_reference(self):
        orphan = line(sid="set_lost", pid=None)
        result = reconcile([], [orphan])[0]
        assert result.status is ReconciliationStatus.UNRESOLVED
        assert result.source_transaction_id == "set_lost"
        assert result.is_exception is True

    def test_unresolved_whitespace_reference_counts_as_missing_usable_ref(
        self,
    ):
        orphan = line(sid="set_lost", pid="   ")
        result = reconcile([], [orphan])[0]
        assert result.status is ReconciliationStatus.UNRESOLVED


class TestEngineContract:
    def test_results_follow_input_order_then_orphans(self):
        payments = [pay("pay_b"), pay("pay_a")]
        settlements = [
            line("set_z", "pay_ghost"),
            line("set_y", None),
            line("set_x", "pay_a"),
            line("set_w", "pay_b"),
        ]
        results = reconcile(payments, settlements)
        # Payments keep input order; orphan lines follow in input order,
        # anchored on their (phantom) reference or own id.
        assert [r.source_transaction_id for r in results] == [
            "pay_b",
            "pay_a",
            "pay_ghost",
            "set_y",
        ]
        # The whitespace-only reference degrades to UNRESOLVED anchored
        # on the settlement line itself.
        assert results[-1].status is ReconciliationStatus.UNRESOLVED

    def test_repeated_runs_are_identical(self):
        payments = [pay(f"pay_{i}", 10_000 * i) for i in range(20)]
        settlements = [
            line(f"set_{i}", f"pay_{i}", 10_000 * i) for i in range(1, 20)
        ] + [line("set_orphan", "pay_missing")]
        first = reconcile(payments, settlements)
        second = reconcile(payments, settlements)
        assert first == second
        assert [r.model_dump() for r in first] == [
            r.model_dump() for r in second
        ]

    def test_inputs_are_never_mutated(self):
        payment = pay()
        settlement_line = line()
        snapshot_payment = payment.model_dump()
        snapshot_line = settlement_line.model_dump()
        reconcile([payment], [settlement_line])
        assert payment.model_dump() == snapshot_payment
        assert settlement_line.model_dump() == snapshot_line

    @pytest.mark.parametrize(
        "dup_payments,dup_lines,error_part",
        [
            ([pay(), pay()], [], "duplicate payment id"),
            ([], [line(), line()], "duplicate settlement id"),
        ],
    )
    def test_duplicate_ids_are_rejected(self, dup_payments, dup_lines, error_part):
        with pytest.raises(ValueError, match=error_part):
            reconcile(dup_payments, dup_lines)

    def test_domain_models_reject_extra_fields_and_bad_types(self):
        with pytest.raises(ValidationError):
            ReconciliationPayment(
                payment_id="p", amount_minor=-1, currency="INR", status="captured"
            )
        with pytest.raises(ValidationError):
            ReconciliationSettlementLine(
                settlement_id="s",
                payment_id="p",
                amount_minor=1,
                currency="INR",
                unexpected=True,
            )

    def test_status_constants_align_with_project_vocabulary(self):
        # Drift guard against app.db.models.payment (kept local so the
        # engine stays database-free).
        from app.db.models.payment import PAYMENT_KNOWN_STATUSES

        assert KNOWN_PAYMENT_STATUSES == frozenset(PAYMENT_KNOWN_STATUSES)
        assert SETTLEMENT_ELIGIBLE_STATUSES <= KNOWN_PAYMENT_STATUSES


# --- metrics ----------------------------------------------------------------------


class TestSummaryMetrics:
    def test_empty_batch_has_zero_denominator_none_rates(self):
        summary = summarize([])
        assert summary.total_records == 0
        assert summary.match_rate is None
        assert summary.throughput_records_per_second == 0.0
        assert summary.exception_breakdown == {}

    def test_match_rate_and_breakdown_math(self):
        results = reconcile(
            [
                pay("pay_ok"),
                pay("pay_amt", amount_minor=500),
                pay("pay_miss"),
                pay("pay_bad", status="failed"),
            ],
            [
                line("set_ok", "pay_ok"),
                line("set_amt", "pay_amt", amount_minor=400),
                line("set_bad", "pay_bad"),
            ],
        )
        summary = summarize(results, processing_seconds=2.0)
        assert summary.total_records == 4
        assert summary.matched_count == 1
        assert summary.exception_count == 3
        assert summary.unresolved_count == 0
        assert summary.match_rate == 25.0
        assert summary.exception_breakdown == {
            "AMOUNT_MISMATCH": 1,
            "INVALID_STATUS": 1,
            "MISSING_SETTLEMENT": 1,
        }
        assert summary.processing_time_ms == 2000.0
        assert summary.throughput_records_per_second == 2.0

    def test_throughput_uses_whole_seconds(self):
        results = [
            ReconciliationResult(
                source_transaction_id=f"p{i}",
                status=ReconciliationStatus.MATCHED,
                reason="amounts match exactly",
            )
            for i in range(10)
        ]
        summary = summarize(results, processing_seconds=0.0)
        assert summary.throughput_records_per_second == 0.0

    def test_report_splits_exceptions_from_results(self):
        report = build_report([pay()], [line()])
        assert len(report.results) == 1
        assert report.exceptions == []
        assert report.summary.match_rate == 100.0


# --- ground truth comparator -------------------------------------------------------


class TestGroundTruthEvaluation:
    def test_perfect_alignment_scores_full_accuracy(self):
        results = reconcile([pay()], [line()])
        truth = [
            GroundTruthEntry(
                case_id="pay_0001",
                expected_status=ReconciliationStatus.MATCHED,
            )
        ]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.accuracy == 100.0
        assert evaluation.correct_decisions == 1
        assert evaluation.mismatches == []

    def test_wrong_and_missing_decisions_are_reported_honestly(self):
        results = reconcile([pay()], [])  # engine says MISSING_SETTLEMENT
        truth = [
            GroundTruthEntry(
                case_id="pay_0001",
                expected_status=ReconciliationStatus.MATCHED,
            ),
            GroundTruthEntry(
                case_id="nowhere",
                expected_status=ReconciliationStatus.MATCHED,
            ),
        ]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.total_cases == 2
        assert evaluation.correct_decisions == 0
        assert evaluation.incorrect_decisions == 1
        assert evaluation.accuracy == 0.0
        assert any(m["actual"] == "<missing>" for m in evaluation.mismatches)

    def test_no_cases_yields_none_accuracy_not_fake_100(self):
        evaluation = evaluate_ground_truth([], [])
        assert evaluation.accuracy is None

# --- financial components: expected-settlement rules (R5/R8/R9) --------


def refund(
    rid="rfnd_0001",
    pid="pay_0001",
    amount_minor=10_000,
    currency="INR",
    status="processed",
):
    from app.schemas.reconciliation import ReconciliationRefund

    return ReconciliationRefund(
        refund_id=rid,
        payment_id=pid,
        amount_minor=amount_minor,
        currency=currency,
        status=status,
    )


class TestExpectedSettlementMath:
    def test_fee_and_tax_deduction_still_matches(self):
        """gross 100000 − fee 2000 − tax 400 = 97600 settles exactly."""
        payment = pay().model_copy(
            update={"fee_minor": 2_000, "tax_minor": 400}
        )
        results = single(payment, line(amount_minor=97_600))
        assert results[0].status is ReconciliationStatus.MATCHED
        assert results[0].expected_amount_minor == 97_600
        assert results[0].fee_minor == 2_000
        assert results[0].tax_minor == 400
        assert "expected net amount" in results[0].reason

    def test_wrong_settlement_becomes_unexplained_difference(self):
        """With fee/tax recorded, any residual difference is UNEXPLAINED —
        never the legacy AMOUNT_MISMATCH."""
        payment = pay().model_copy(
            update={"fee_minor": 2_000, "tax_minor": 400}
        )
        results = single(payment, line(amount_minor=97_550))
        assert (
            results[0].status
            is ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
        )
        assert results[0].difference_minor == -50

    def test_identical_numbers_without_components_stay_amount_mismatch(self):
        """No fee/tax/refunds recorded → legacy AMOUNT_MISMATCH semantics."""
        results = single(pay(), line(amount_minor=97_600))
        assert results[0].status is ReconciliationStatus.AMOUNT_MISMATCH
        assert results[0].difference_minor == 97_600 - 100_000

    def test_refund_with_fees_nets_correctly(self):
        """gross 100000 − refund 50000 − fee 2000 − tax 400 = 47600."""
        payment = pay(amount_minor=100_000).model_copy(
            update={"fee_minor": 2_000, "tax_minor": 400}
        )
        r = refund(amount_minor=50_000)
        results = reconcile([payment], [line(amount_minor=47_600)], [r])
        assert results[0].status is ReconciliationStatus.MATCHED
        assert results[0].expected_amount_minor == 47_600
        assert results[0].refunded_total_minor == 50_000

    def test_full_refund_with_unrebated_fees_is_honestly_negative(self):
        """Fee rebates are NOT modeled (undocumented provider behavior):
        a full refund leaves the expectation at −fee−tax, and settling
        zero cannot match it. The engine reports the residual honestly."""
        payment = pay(amount_minor=100_000).model_copy(
            update={"fee_minor": 2_000, "tax_minor": 400}
        )
        r = refund(amount_minor=100_000)
        results = reconcile([payment], [line(amount_minor=0)], [r])
        assert (
            results[0].status
            is ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
        )
        assert results[0].expected_amount_minor == -(2_000 + 400)
        assert results[0].refunded_total_minor == 100_000

    def test_multiple_partial_refunds_sum_into_the_expectation(self):
        payment = pay(amount_minor=100_000)
        refunds = [
            refund(rid="r1", amount_minor=30_000),
            refund(rid="r2", amount_minor=20_000),
        ]
        results = reconcile([payment], [line(amount_minor=50_000)], refunds)
        assert results[0].status is ReconciliationStatus.MATCHED
        assert results[0].refunded_total_minor == 50_000


class TestRefundIntegrityRule:
    def test_over_refund_is_flagged_before_any_settlement_math(self):
        payment = pay(amount_minor=100_000)
        refunds = [
            refund(rid="r1", amount_minor=60_000),
            refund(rid="r2", amount_minor=60_000),  # total 120k > 100k
        ]
        results = reconcile([payment], [line(amount_minor=100_000)], refunds)
        assert results[0].status is ReconciliationStatus.REFUND_MISMATCH
        assert "exceeding the payment amount" in results[0].reason
        # No valid comparison happened: difference stays unset.
        assert results[0].difference_minor is None

    def test_cross_currency_refund_is_flagged(self):
        payment = pay(amount_minor=100_000)
        results = reconcile(
            [payment],
            [line(amount_minor=100_000)],
            [refund(currency="USD")],
        )
        assert results[0].status is ReconciliationStatus.REFUND_MISMATCH
        assert "different currency" in results[0].reason

    def test_orphan_processed_refund_is_missing_payment_on_the_refund_id(self):
        results = reconcile(
            [pay(pid="pay_a")],
            [line(sid="s_a", pid="pay_a")],  # keep the payment itself clean
            [refund(pid="pay_ghost")],
        )
        by_source = {r.source_transaction_id: r for r in results}
        orphan = by_source["rfnd_0001"]
        assert orphan.status is ReconciliationStatus.MISSING_PAYMENT
        assert "parent payment" in orphan.reason
        assert by_source["pay_a"].status is ReconciliationStatus.MATCHED

    def test_pending_and_failed_refunds_are_inert(self):
        """Only processed refunds moved money; everything else is noise."""
        payment = pay(amount_minor=100_000)
        inert = [
            refund(rid="r1", status="pending", amount_minor=90_000),
            refund(rid="r2", status="failed", amount_minor=90_000),
        ]
        results = reconcile([payment], [line()], inert)
        assert results[0].status is ReconciliationStatus.MATCHED
        assert results[0].refunded_total_minor is None

    def test_duplicate_refund_ids_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate refund id"):
            reconcile(
                [pay()],
                [line()],
                [refund(rid="same"), refund(rid="same")],
            )


class TestSettlementTimingRule:
    def _dated_pair(self, delay_days):
        from datetime import date, timedelta

        created = date(2026, 3, 1)
        settled = created + timedelta(days=delay_days)
        payment = pay().model_copy(update={"created_on": created})
        settlement_line = line().model_copy(update={"settled_on": settled})
        return payment, settlement_line

    def test_within_tolerance_matches(self):
        payment, settlement_line = self._dated_pair(3)
        results = reconcile(
            [payment],
            [settlement_line],
            max_settlement_delay_days=3,
        )
        assert results[0].status is ReconciliationStatus.MATCHED

    def test_beyond_tolerance_is_flagged_as_delay(self):
        payment, settlement_line = self._dated_pair(9)
        results = reconcile(
            [payment],
            [settlement_line],
            max_settlement_delay_days=3,
        )
        assert results[0].status is ReconciliationStatus.SETTLEMENT_DELAY
        assert "beyond the configured tolerance" in results[0].reason
        assert results[0].difference_minor == 0  # money was exact

    def test_rule_disabled_by_default_keeps_legacy_behavior(self):
        """Same late data, no window configured → plain match (R6.4)."""
        payment, settlement_line = self._dated_pair(30)
        results = reconcile([payment], [settlement_line])
        assert results[0].status is ReconciliationStatus.MATCHED

    def test_missing_dates_skip_timing_check(self):
        """Timing needs both clocks; absent dates are never fabricated."""
        payment = pay().model_copy(update={"created_on": None})
        settlement_line = line()
        results = reconcile(
            [payment],
            [settlement_line],
            max_settlement_delay_days=3,
        )
        assert results[0].status is ReconciliationStatus.MATCHED


class TestSingleSettlementFlowRegressions:
    """Cases A-H pinning the refactored single payment + settlement flow:
    R8 integrity precedes any amount comparison, exactly one exact-match
    branch exists, and the delay window is evaluated once (primary when
    money is exact, secondary on a financial mismatch)."""

    def test_a_valid_refund_and_exact_settlement_matches(self):
        results = reconcile(
            [pay(amount_minor=100_000)],
            [line(amount_minor=90_000)],
            [refund(rid="r1", amount_minor=10_000)],
        )
        assert results[0].status is ReconciliationStatus.MATCHED
        assert results[0].expected_amount_minor == 90_000
        assert results[0].refunded_total_minor == 10_000
        assert results[0].secondary_issues == ()

    def test_b_over_refund_with_exact_settlement_is_refund_mismatch(self):
        refunds = [
            refund(rid="r1", amount_minor=60_000),
            refund(rid="r2", amount_minor=60_000),  # total 120k > 100k
        ]
        results = reconcile(
            [pay(amount_minor=100_000)],
            [line(amount_minor=100_000)],
            refunds,
        )
        assert results[0].status is ReconciliationStatus.REFUND_MISMATCH
        assert "exceeding the payment amount" in results[0].reason
        assert results[0].difference_minor is None

    def test_c_over_refund_with_wrong_amount_still_refund_mismatch(self):
        """R8 fires regardless of the settlement amount (regression:
        anomalies used to be missed whenever the amounts differed)."""
        refunds = [
            refund(rid="r1", amount_minor=60_000),
            refund(rid="r2", amount_minor=60_000),
        ]
        results = reconcile(
            [pay(amount_minor=100_000)],
            [line(amount_minor=80_000)],  # wrong on top of the anomaly
            refunds,
        )
        assert results[0].status is ReconciliationStatus.REFUND_MISMATCH
        assert results[0].difference_minor is None

    def test_d_cross_currency_refund_with_wrong_amount_is_refund_mismatch(
        self,
    ):
        results = reconcile(
            [pay(amount_minor=100_000)],
            [line(amount_minor=50_000)],  # wrong on top of the anomaly
            [refund(rid="r1", amount_minor=10_000, currency="USD")],
        )
        assert results[0].status is ReconciliationStatus.REFUND_MISMATCH
        assert "different currency" in results[0].reason
        assert results[0].difference_minor is None

    def test_e_plain_mismatch_without_components_is_amount_mismatch(self):
        results = reconcile(
            [pay(amount_minor=250_000)],
            [line(amount_minor=249_999)],
        )
        assert results[0].status is ReconciliationStatus.AMOUNT_MISMATCH
        assert results[0].exception_type is ReconciliationStatus.AMOUNT_MISMATCH
        assert results[0].difference_minor == -1
        assert results[0].secondary_issues == ()

    def test_f_fee_tax_mismatch_is_unexplained_difference(self):
        payment = pay().model_copy(
            update={"fee_minor": 2_000, "tax_minor": 400}
        )
        results = reconcile([payment], [line(amount_minor=97_550)])
        assert (
            results[0].status
            is ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
        )
        assert results[0].difference_minor == -50

    def test_g_exact_amount_but_late_is_settlement_delay(self):
        from datetime import date, timedelta

        created = date(2026, 3, 1)
        payment = pay().model_copy(update={"created_on": created})
        settlement_line = line().model_copy(
            update={"settled_on": created + timedelta(days=9)}
        )
        results = reconcile(
            [payment],
            [settlement_line],
            max_settlement_delay_days=3,
        )
        assert results[0].status is ReconciliationStatus.SETTLEMENT_DELAY
        assert results[0].difference_minor == 0  # money was exact
        assert results[0].secondary_issues == ()

    def test_h_wrong_amount_and_late_keeps_financial_verdict_with_delay_secondary(
        self,
    ):
        """Financial correctness outranks timeliness: the mismatch wins
        primary classification and SETTLEMENT_DELAY demotes to
        ``secondary_issues`` instead of replacing it."""
        from datetime import date, timedelta

        created = date(2026, 3, 1)
        settled_on = created + timedelta(days=9)
        # Component-backed shape → UNEXPLAINED_SETTLEMENT_DIFFERENCE.
        payment = pay().model_copy(
            update={"fee_minor": 2_000, "tax_minor": 400, "created_on": created}
        )
        settlement_line = line(amount_minor=97_550).model_copy(
            update={"settled_on": settled_on}
        )
        results = reconcile(
            [payment],
            [settlement_line],
            max_settlement_delay_days=3,
        )
        assert (
            results[0].status
            is ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
        )
        assert results[0].secondary_issues == (
            ReconciliationStatus.SETTLEMENT_DELAY,
        )
        assert results[0].difference_minor == -50
        # Legacy shape (no components) → AMOUNT_MISMATCH primary, same
        # secondary issue attached.
        legacy_payment = pay().model_copy(update={"created_on": created})
        legacy_line = line(amount_minor=97_550).model_copy(
            update={"settled_on": settled_on}
        )
        legacy_results = reconcile(
            [legacy_payment],
            [legacy_line],
            max_settlement_delay_days=3,
        )
        assert legacy_results[0].status is ReconciliationStatus.AMOUNT_MISMATCH
        assert legacy_results[0].secondary_issues == (
            ReconciliationStatus.SETTLEMENT_DELAY,
        )


class TestLegacyEquivalence:
    def test_legacy_inputs_reproduce_pre_component_behavior(self):
        """No refunds/components/dates ⇒ identical decisions to R0-R7 era:
        same statuses, amounts, and reasons for a mixed batch."""
        payments = [
            pay("p_ok"),
            pay("p_miss"),
            pay("p_cur", currency="USD"),
        ]
        settlements = [
            line("s_ok", "p_ok", 100_000),
            line("s_cur", "p_cur", 100_000, "INR"),
            line("s_ghost", "p_never_existed", 500),
            line("s_anon", None, 700),
        ]
        results = reconcile(payments, settlements)
        by_source = {r.source_transaction_id: r for r in results}
        assert by_source["p_ok"].status is ReconciliationStatus.MATCHED
        assert by_source["p_miss"].status is (
            ReconciliationStatus.MISSING_SETTLEMENT
        )
        assert by_source["p_cur"].status is (
            ReconciliationStatus.CURRENCY_MISMATCH
        )
        assert by_source["p_never_existed"].status is (
            ReconciliationStatus.MISSING_PAYMENT
        )
        assert by_source["s_anon"].status is ReconciliationStatus.UNRESOLVED


class TestDetectionMetrics:
    def _entry(self, case_id, status):
        return GroundTruthEntry(case_id=case_id, expected_status=status)

    def test_false_positives_negatives_precision_recall_f1(self):
        results = [
            self._result("tp", ReconciliationStatus.AMOUNT_MISMATCH),
            self._result("fp", ReconciliationStatus.AMOUNT_MISMATCH),
            self._result("tn", ReconciliationStatus.MATCHED),
            self._result("fn", ReconciliationStatus.MATCHED),
        ]
        truth = [
            self._entry("tp", ReconciliationStatus.AMOUNT_MISMATCH),
            self._entry("fp", ReconciliationStatus.MATCHED),
            self._entry("tn", ReconciliationStatus.MATCHED),
            self._entry("fn", ReconciliationStatus.AMOUNT_MISMATCH),
        ]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.false_positives == 1
        assert evaluation.false_negatives == 1
        assert evaluation.precision == 50.0
        assert evaluation.recall == 50.0
        assert evaluation.f1 == 50.0

    def _result(self, source_id, status):
        return ReconciliationResult(
            source_transaction_id=source_id,
            status=status,
            reason="test",
            exception_type=(
                status if status is not ReconciliationStatus.MATCHED else None
            ),
        )

    def test_empty_truth_yields_none_not_fabricated_zeroes(self):
        evaluation = evaluate_ground_truth([], [])
        assert evaluation.accuracy is None
        assert evaluation.precision is None
        assert evaluation.recall is None
        assert evaluation.f1 is None

    def test_no_predicted_positives_gives_none_precision(self):
        results = [self._result("a", ReconciliationStatus.MATCHED)]
        truth = [self._entry("a", ReconciliationStatus.MATCHED)]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.precision is None
        assert evaluation.recall is None
        assert evaluation.f1 is None

    def test_amount_and_reason_pins_are_verified(self):
        results = [
            ReconciliationResult(
                source_transaction_id="c1",
                status=ReconciliationStatus.AMOUNT_MISMATCH,
                expected_amount_minor=999,  # wrong on purpose
                actual_amount_minor=500,
                reason="generic",
                exception_type=ReconciliationStatus.AMOUNT_MISMATCH,
            )
        ]
        truth = [
            GroundTruthEntry(
                case_id="c1",
                expected_status=ReconciliationStatus.AMOUNT_MISMATCH,
                expected_amount_minor=1000,
                actual_amount_minor=500,
                expected_reason_fragment="differs",
            )
        ]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.correct_decisions == 0
        kinds = {m["kind"] for m in evaluation.mismatches}
        assert kinds == {"expected_amount", "reason"}

    def test_matching_secondary_issue_expectation_passes(self):
        results = [
            ReconciliationResult(
                source_transaction_id="c1",
                status=ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE,
                reason="differs",
                exception_type=(
                    ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
                ),
                secondary_issues=(ReconciliationStatus.SETTLEMENT_DELAY,),
            )
        ]
        truth = [
            GroundTruthEntry(
                case_id="c1",
                expected_status=(
                    ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
                ),
                expected_secondary_issues=(
                    ReconciliationStatus.SETTLEMENT_DELAY,
                ),
            )
        ]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.correct_decisions == 1
        assert evaluation.mismatches == []

    def test_missing_secondary_issue_is_flagged(self):
        """Truth pins a compound finding; the engine reported none."""
        results = [
            ReconciliationResult(
                source_transaction_id="c1",
                status=ReconciliationStatus.AMOUNT_MISMATCH,
                reason="differs",
                exception_type=ReconciliationStatus.AMOUNT_MISMATCH,
                secondary_issues=(),
            )
        ]
        truth = [
            GroundTruthEntry(
                case_id="c1",
                expected_status=ReconciliationStatus.AMOUNT_MISMATCH,
                expected_secondary_issues=(
                    ReconciliationStatus.SETTLEMENT_DELAY,
                ),
            )
        ]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.correct_decisions == 0
        assert len(evaluation.mismatches) == 1
        mismatch = evaluation.mismatches[0]
        assert mismatch["kind"] == "secondary_issues"
        assert mismatch["expected"] == "('SETTLEMENT_DELAY',)"
        assert mismatch["actual"] == "()"

    def test_unexpected_secondary_issue_is_flagged(self):
        """The engine fabricated a compound finding the truth denies."""
        results = [
            ReconciliationResult(
                source_transaction_id="c1",
                status=ReconciliationStatus.AMOUNT_MISMATCH,
                reason="differs",
                exception_type=ReconciliationStatus.AMOUNT_MISMATCH,
                secondary_issues=(ReconciliationStatus.SETTLEMENT_DELAY,),
            )
        ]
        truth = [
            GroundTruthEntry(
                case_id="c1",
                expected_status=ReconciliationStatus.AMOUNT_MISMATCH,
                expected_secondary_issues=(),  # none expected
            )
        ]
        evaluation = evaluate_ground_truth(results, truth)
        assert evaluation.correct_decisions == 0
        assert len(evaluation.mismatches) == 1
        mismatch = evaluation.mismatches[0]
        assert mismatch["kind"] == "secondary_issues"
        assert mismatch["expected"] == "()"
        assert mismatch["actual"] == "('SETTLEMENT_DELAY',)"

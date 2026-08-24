"""Synthetic reconciliation dataset tests.

Pins the determinism contract (same seed+size ⇒ identical batch), the
case-type distribution, ground-truth integrity, and — critically — that
the engine's decisions agree with every generated expectation on the
default 100-record batch. Also guards the distribution constant against
drift and aligns the generator's status vocabulary with the engine's.
"""

import pytest

from app.schemas.reconciliation import (
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    KNOWN_PAYMENT_STATUSES,
    GroundTruthEntry,
    ReconciliationStatus,
)
from app.services.reconciliation import (
    build_report,
    evaluate_ground_truth,
)
from app.services.reconciliation_synthetic import (
    CASE_DISTRIBUTION,
    DEFAULT_SEED,
    DEFAULT_SIZE,
    generate_synthetic_batch,
)


class TestDeterminism:
    def test_same_seed_and_size_produce_identical_batches(self):
        first = generate_synthetic_batch(seed=42, size=100)
        second = generate_synthetic_batch(seed=42, size=100)
        assert first.model_dump_json() == second.model_dump_json()

    def test_repeated_runs_reconcile_identically(self):
        batch_a = generate_synthetic_batch(seed=7, size=60)
        batch_b = generate_synthetic_batch(seed=7, size=60)
        report_a = build_report(batch_a.payments, batch_a.settlements)
        report_b = build_report(batch_b.payments, batch_b.settlements)
        # Timing fields are the only legitimate run-to-run variance.
        payload_a = report_a.model_dump()
        payload_b = report_b.model_dump()
        for payload in (payload_a, payload_b):
            payload["summary"].pop("processing_time_ms")
            payload["summary"].pop("throughput_records_per_second")
        assert payload_a == payload_b

    def test_different_seed_relocates_cases(self):
        a = generate_synthetic_batch(seed=1, size=100)
        b = generate_synthetic_batch(seed=2, size=100)
        # Identical case counts by design; the seed is embedded in ids,
        # so the batches are never identical.
        a_ids = {p.payment_id for p in a.payments}
        b_ids = {p.payment_id for p in b.payments}
        assert a_ids != b_ids
        assert len(a.ground_truth) == len(b.ground_truth)
        assert sorted(
            e.expected_status.value for e in a.ground_truth
        ) == sorted(e.expected_status.value for e in b.ground_truth)

    def test_negative_size_is_rejected(self):
        with pytest.raises(ValueError):
            generate_synthetic_batch(seed=1, size=-5)


class TestDistributionAndIntegrity:
    def test_distribution_constant_sums_to_100(self):
        assert sum(CASE_DISTRIBUTION.values()) == 100
        assert set(CASE_DISTRIBUTION) == set(ReconciliationStatus)

    def test_default_batch_has_at_least_100_records(self):
        batch = generate_synthetic_batch()
        assert len(batch.ground_truth) == DEFAULT_SIZE >= 100

    def test_default_seed_is_pinned_for_the_benchmark(self):
        assert DEFAULT_SEED == 42

    def test_hundred_record_batch_matches_designed_distribution(self):
        batch = generate_synthetic_batch(seed=42, size=100)
        counts: dict[ReconciliationStatus, int] = {}
        for entry in batch.ground_truth:
            counts[entry.expected_status] = (
                counts.get(entry.expected_status, 0) + 1
            )
        assert counts == CASE_DISTRIBUTION

    def test_scaled_size_50_keeps_every_case_type_present(self):
        batch = generate_synthetic_batch(seed=9, size=50)
        present = {entry.expected_status for entry in batch.ground_truth}
        assert present == set(ReconciliationStatus)
        assert len(batch.ground_truth) == 50

    def test_ground_truth_covers_all_documented_exception_types(self):
        batch = generate_synthetic_batch(seed=42, size=200)
        expected_types = set(ReconciliationStatus)
        assert {e.expected_status for e in batch.ground_truth} == expected_types

    def test_compound_cases_pin_expected_secondary_issues(self):
        """The canonical batch intentionally generates compound cases:
        unexplained differences arriving late keep their financial
        primary verdict and must pin SETTLEMENT_DELAY as the expected
        secondary issue (evaluated end-to-end by the accuracy test)."""
        batch = generate_synthetic_batch(
            seed=DEFAULT_SEED, size=DEFAULT_SIZE
        )
        compound = [
            e for e in batch.ground_truth if e.expected_secondary_issues
        ]
        assert compound, (
            "the canonical batch must exercise compound exceptions"
        )
        for entry in compound:
            assert entry.expected_status is (
                ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE
            )
            assert entry.expected_secondary_issues == (
                ReconciliationStatus.SETTLEMENT_DELAY,
            )
            assert entry.actual_amount_minor is not None
            assert entry.actual_amount_minor < entry.expected_amount_minor

    def test_all_amounts_are_non_negative_minor_unit_integers(self):
        batch = generate_synthetic_batch(seed=3, size=80)
        assert all(p.amount_minor >= 0 for p in batch.payments)
        assert all(s.amount_minor >= 0 for s in batch.settlements)

    def test_healthy_payment_statuses_stay_inside_known_vocabulary(self):
        """INVALID_STATUS cases deliberately use out-of-vocabulary or
        ineligible statuses; every other payment must use real ones."""
        batch = generate_synthetic_batch(seed=5, size=150)
        invalid_case_ids = {
            entry.payment_id
            for entry in batch.ground_truth
            if entry.expected_status is ReconciliationStatus.INVALID_STATUS
        }
        healthy = [
            p for p in batch.payments if p.payment_id not in invalid_case_ids
        ]
        assert healthy, "expected healthy payments in the batch"
        assert {p.status for p in healthy} <= KNOWN_PAYMENT_STATUSES

    def test_duplicate_cases_emit_two_lines_sharing_one_reference(self):
        batch = generate_synthetic_batch(seed=11, size=100)
        duplicates = [
            e
            for e in batch.ground_truth
            if e.expected_status is ReconciliationStatus.DUPLICATE_SETTLEMENT
        ]
        assert duplicates, "distribution guarantees duplicate cases"
        line_ids = {s.settlement_id for s in batch.settlements}
        for entry in duplicates:
            assert len(entry.settlement_ids) == 2
            assert set(entry.settlement_ids) <= line_ids

    def test_settlement_ids_are_unique_across_the_batch(self):
        batch = generate_synthetic_batch(seed=13, size=120)
        ids = [s.settlement_id for s in batch.settlements]
        assert len(ids) == len(set(ids))


class TestGroundTruthDiscipline:
    def test_engine_agrees_with_every_expectation_on_default_batch(self):
        """THE Track 04 evaluation invariant: accuracy is exactly 100%.

        The generator's corruption cases are engineered so each maps to
        exactly one deterministic rule; if this ever fails, either the
        engine or the dataset drifted from the documented rules.
        """
        batch = generate_synthetic_batch(seed=DEFAULT_SEED, size=DEFAULT_SIZE)
        report = build_report(
            batch.payments,
            batch.settlements,
            batch.refunds,
            max_settlement_delay_days=DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
        )
        evaluation = evaluate_ground_truth(report.results, batch.ground_truth)
        assert evaluation.accuracy == 100.0
        assert evaluation.mismatches == []
        assert evaluation.false_positives == 0
        assert evaluation.false_negatives == 0
        assert report.summary.total_records == DEFAULT_SIZE
        assert report.summary.exception_count == sum(
            count
            for status, count in CASE_DISTRIBUTION.items()
            if status is not ReconciliationStatus.MATCHED
        )

    def test_engine_signature_accepts_only_records_never_outcomes(self):
        """Structural guard: reconcile() has no ground-truth channel.

        Legitimate parameters are batch records (payments, settlements,
        refunds) plus configuration knobs (the settlement-delay window).
        Anything outcome-like must never appear.
        """
        import inspect

        from app.services import reconciliation as engine_module

        signature = inspect.signature(engine_module.reconcile)
        param_names = set(signature.parameters)
        assert param_names == {
            "payments",
            "settlements",
            "refunds",
            "max_settlement_delay_days",
        }
        forbidden_fragments = ("truth", "expected", "outcome", "label")
        for name in param_names:
            assert not any(f in name for f in forbidden_fragments), name

    def test_ground_truth_entries_are_evaluation_only_models(self):
        entry = GroundTruthEntry(
            case_id="x", expected_status=ReconciliationStatus.MATCHED
        )
        encoded = entry.model_dump(mode="json")
        assert encoded["expected_status"] == "MATCHED"

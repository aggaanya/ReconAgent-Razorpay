"""Tests for deterministic What-Changed / Drift Analysis."""

from app.services.reconciliation import build_report
from app.services.reconciliation_drift import compare_runs
from app.services.reconciliation_synthetic import generate_synthetic_batch


def test_drift_analysis_identical_runs():
    batch1 = generate_synthetic_batch(seed=42, size=100)
    report1 = build_report(batch1.payments, batch1.settlements, batch1.refunds)

    batch2 = generate_synthetic_batch(seed=42, size=100)
    report2 = build_report(batch2.payments, batch2.settlements, batch2.refunds)

    drift = compare_runs(report1, report2, previous_seed=42, current_seed=42)

    assert drift.match_rate_change_pp == 0.0
    assert drift.exception_count_change == 0
    assert drift.financial_exposure_change_minor == 0
    for cd in drift.category_drifts:
        assert cd.count_change == 0
        assert cd.exposure_change_minor == 0


def test_drift_analysis_different_seeds():
    batch_prev = generate_synthetic_batch(seed=41, size=100)
    report_prev = build_report(batch_prev.payments, batch_prev.settlements, batch_prev.refunds)

    batch_curr = generate_synthetic_batch(seed=42, size=100)
    report_curr = build_report(batch_curr.payments, batch_curr.settlements, batch_curr.refunds)

    drift = compare_runs(report_prev, report_curr, previous_seed=41, current_seed=42)

    assert drift.previous_seed == 41
    assert drift.current_seed == 42
    assert drift.previous_match_rate == report_prev.summary.match_rate
    assert drift.current_match_rate == report_curr.summary.match_rate
    assert drift.match_rate_change_pp is not None
    assert isinstance(drift.major_drivers, list)
    assert len(drift.major_drivers) > 0
    assert len(drift.category_drifts) > 0
    assert len(drift.priority_drifts) == 4  # CRITICAL, HIGH, MEDIUM, LOW

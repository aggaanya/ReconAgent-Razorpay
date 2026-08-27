"""Tests for deterministic What-Changed / Drift Analysis."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.ai import _drift_narration_signals, _render_drift_narrative
from app.schemas.reconciliation import DEFAULT_MAX_SETTLEMENT_DELAY_DAYS
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
    report_prev = build_report(
        batch_prev.payments, batch_prev.settlements, batch_prev.refunds
    )

    batch_curr = generate_synthetic_batch(seed=42, size=100)
    report_curr = build_report(
        batch_curr.payments, batch_curr.settlements, batch_curr.refunds
    )

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


def test_compare_endpoint_returns_deterministic_facts_without_llm(monkeypatch):
    def fail_if_called():
        pytest.fail("What-Changed facts must not require an LLM")

    monkeypatch.setattr("app.api.ai.get_ai_agent", fail_if_called)

    response = TestClient(app).post(
        "/api/v1/ai/reconcile/compare",
        json={"previous_seed": 41, "current_seed": 42},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert body["errors"] == []
    assert body["current_summary"]["total_records"] == 100
    assert body["current_summary"]["matched_count"] == 58
    assert body["current_summary"]["exception_count"] == 42
    assert body["current_summary"]["unresolved_count"] == 2
    assert body["current_summary"]["match_rate"] == 58.0
    assert body["drift"]["current_financial_exposure_minor"] == 16065382


def test_compare_explanation_is_safe_and_deterministic(monkeypatch):
    def fail_if_called():
        pytest.fail("Compare narration must not require an LLM")

    monkeypatch.setattr("app.api.ai.get_ai_agent", fail_if_called)

    response = TestClient(app).post(
        "/api/v1/ai/reconcile/compare",
        json={"previous_seed": 41, "current_seed": 42, "explain": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert body["answer"] is not None
    assert (
        "REFUND_MISMATCH: count remained unchanged at 7; exposure increased by ₹10,340.25."
        in body["answer"]
    )
    assert "₹160,653.82" in body["answer"]
    assert "160653820" not in body["answer"]


def test_drift_narration_separates_count_and_exposure_units():
    def make_report(seed):
        batch = generate_synthetic_batch(seed=seed, size=100)
        return build_report(
            batch.payments,
            batch.settlements,
            batch.refunds,
            max_settlement_delay_days=DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
        )

    previous = make_report(41)
    current = make_report(42)
    drift = compare_runs(previous, current, previous_seed=41, current_seed=42)
    facts = _drift_narration_signals(
        drift, previous.exception_summary, current.exception_summary
    )

    refund = next(
        item for item in facts["categories"] if item["category"] == "REFUND_MISMATCH"
    )
    assert refund["count_statement"] == "count remained unchanged at 7"
    assert refund["exposure_statement"] == "exposure increased by ₹10,340.25"
    assert facts["current_total_exposure_display"] == "₹160,653.82"
    assert facts["unresolved_exposure_display"].startswith("₹")
    for item in facts["categories"] + facts["severity_levels"]:
        if "remained unchanged" in item["count_statement"]:
            assert "increased" not in item["count_statement"]
            assert "decreased" not in item["count_statement"]

    narrative = _render_drift_narrative(facts)
    assert (
        "REFUND_MISMATCH: count remained unchanged at 7; exposure increased by ₹10,340.25."
        in narrative
    )
    assert "160,653.82" in narrative
    assert "160653820" not in narrative

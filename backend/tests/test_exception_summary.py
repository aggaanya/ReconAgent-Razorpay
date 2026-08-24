"""Tests for aggregate exception summary calculations."""

from app.schemas.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
    ExceptionSummary,
)
from app.services.reconciliation import build_exception_summary, build_report
from app.services.reconciliation_synthetic import generate_synthetic_batch


def test_build_exception_summary_basic():
    results = [
        ReconciliationResult(
            source_transaction_id="pay_1",
            status=ReconciliationStatus.MATCHED,
            reason="Matched",
        ),
        ReconciliationResult(
            source_transaction_id="pay_2",
            status=ReconciliationStatus.MISSING_SETTLEMENT,
            expected_amount_minor=5000,
            severity="HIGH",
            priority=85,
            financial_impact_minor=5000,
            reason="Missing settlement",
        ),
        ReconciliationResult(
            source_transaction_id="pay_3",
            status=ReconciliationStatus.DUPLICATE_SETTLEMENT,
            actual_amount_minor=12000,
            severity="CRITICAL",
            priority=100,
            financial_impact_minor=12000,
            reason="Duplicate settlement",
        ),
    ]

    summary = build_exception_summary(results)
    assert isinstance(summary, ExceptionSummary)
    assert summary.total_exceptions == 2
    assert summary.total_financial_exposure_minor == 17000
    assert summary.critical_count == 1
    assert summary.high_count == 1
    assert summary.critical_exposure_minor == 12000
    assert summary.high_exposure_minor == 5000
    assert len(summary.top_impact_exceptions) == 2
    assert summary.top_impact_exceptions[0]["source_transaction_id"] == "pay_3"


def test_exception_summary_in_full_report():
    batch = generate_synthetic_batch(seed=42, size=100)
    report = build_report(batch.payments, batch.settlements, batch.refunds)

    assert report.exception_summary is not None
    summary = report.exception_summary
    assert summary.total_exceptions == report.summary.exception_count
    assert summary.total_financial_exposure_minor > 0
    assert summary.critical_count + summary.high_count + summary.medium_count + summary.low_count == summary.total_exceptions
    assert summary.critical_exposure_minor + summary.high_exposure_minor + summary.medium_exposure_minor + summary.low_exposure_minor == summary.total_financial_exposure_minor

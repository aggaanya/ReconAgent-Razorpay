"""Tests for financial impact calculation and impact-aware prioritization."""

from app.schemas.reconciliation import (
    ReconciliationResult,
    ReconciliationStatus,
)
from app.services.reconciliation import build_report, reconcile
from app.services.reconciliation_policy import (
    compute_financial_impact,
    priority_for,
    annotate_triage,
)
from app.services.reconciliation_synthetic import generate_synthetic_batch


def test_financial_impact_computation_per_status():
    # AMOUNT_MISMATCH
    r_mismatch = ReconciliationResult(
        source_transaction_id="pay_1",
        status=ReconciliationStatus.AMOUNT_MISMATCH,
        expected_amount_minor=1000,
        actual_amount_minor=900,
        difference_minor=-100,
        reason="Amount mismatch",
    )
    impact, reason = compute_financial_impact(r_mismatch)
    assert impact == 100
    assert "100 minor units" in reason

    # UNEXPLAINED_SETTLEMENT_DIFFERENCE
    r_unexplained = ReconciliationResult(
        source_transaction_id="pay_2",
        status=ReconciliationStatus.UNEXPLAINED_SETTLEMENT_DIFFERENCE,
        expected_amount_minor=1000,
        actual_amount_minor=750,
        difference_minor=-250,
        reason="Unexplained difference",
    )
    impact, reason = compute_financial_impact(r_unexplained)
    assert impact == 250
    assert "250 minor units" in reason

    # MISSING_SETTLEMENT
    r_missing_set = ReconciliationResult(
        source_transaction_id="pay_3",
        status=ReconciliationStatus.MISSING_SETTLEMENT,
        expected_amount_minor=5000,
        reason="Missing settlement",
    )
    impact, reason = compute_financial_impact(r_missing_set)
    assert impact == 5000

    # MISSING_PAYMENT
    r_missing_pay = ReconciliationResult(
        source_transaction_id="set_1",
        status=ReconciliationStatus.MISSING_PAYMENT,
        actual_amount_minor=3000,
        reason="Missing payment",
    )
    impact, reason = compute_financial_impact(r_missing_pay)
    assert impact == 3000

    # SETTLEMENT_DELAY
    r_delay = ReconciliationResult(
        source_transaction_id="pay_4",
        status=ReconciliationStatus.SETTLEMENT_DELAY,
        expected_amount_minor=2000,
        actual_amount_minor=2000,
        difference_minor=0,
        reason="Delayed",
    )
    impact, reason = compute_financial_impact(r_delay)
    assert impact == 0

    # MATCHED
    r_matched = ReconciliationResult(
        source_transaction_id="pay_5",
        status=ReconciliationStatus.MATCHED,
        expected_amount_minor=2000,
        actual_amount_minor=2000,
        difference_minor=0,
        reason="Matched",
    )
    impact, reason = compute_financial_impact(r_matched)
    assert impact is None
    assert reason is None


def test_impact_aware_prioritization():
    # HIGH base priority is 80
    # No impact -> 80
    p_base = priority_for(ReconciliationStatus.MISSING_SETTLEMENT, 0)
    assert p_base == 80

    # Small impact -> 80
    p_small = priority_for(ReconciliationStatus.MISSING_SETTLEMENT, 500)  # < ₹100
    assert p_small == 80

    # Medium impact (₹500 = 50,000 minor units -> +5 boost) -> 85
    p_med = priority_for(ReconciliationStatus.MISSING_SETTLEMENT, 50_000)
    assert p_med == 85

    # Very high impact (₹200,000 = 20,000,000 minor units -> capped at +15 boost) -> 95
    p_large = priority_for(ReconciliationStatus.MISSING_SETTLEMENT, 20_000_000)
    assert p_large == 95

    # CRITICAL base priority is 100, stays 100
    p_crit = priority_for(ReconciliationStatus.DUPLICATE_SETTLEMENT, 50_000_000)
    assert p_crit == 100

    # LOW base priority is 30, delay has 0 impact -> 30
    p_low = priority_for(ReconciliationStatus.SETTLEMENT_DELAY, 0)
    assert p_low == 30


def test_synthetic_batch_annotated_with_financial_impact():
    batch = generate_synthetic_batch(seed=42, size=50)
    report = build_report(batch.payments, batch.settlements, batch.refunds)
    
    # Check that exceptions have financial impact attached
    for exc in report.exceptions:
        assert hasattr(exc, "financial_impact_minor")
        assert hasattr(exc, "financial_impact_reason")
        if exc.status is not ReconciliationStatus.SETTLEMENT_DELAY:
            assert exc.financial_impact_minor is not None
            assert exc.financial_impact_minor >= 0
            assert exc.financial_impact_reason is not None
        assert exc.evidence is not None
        assert exc.evidence.financial_impact_minor == exc.financial_impact_minor
        assert exc.evidence.financial_impact_reason == exc.financial_impact_reason

"""Signal Analysis Engine tests — exact values, fully deterministic.

No LLM, no database, no provider: the analyzer consumes plain tool
envelopes and must produce byte-stable typed signals. Every threshold
boundary is tested at its exact edge.
"""

import json
from copy import deepcopy

import pytest

from app.schemas.signals import FinancialSignal
from app.services.signal_analysis import (
    DEFAULT_THRESHOLDS,
    SignalAnalyzer,
    SignalThresholds,
    analyze_signals,
)


# --- envelope builders (mirroring Finance Tool output shapes) -----------------


def trend_envelope(
    metric="gross_revenue",
    current=800_000,
    previous=1_000_000,
    absolute=None,
    pct=None,
    change_type="normal",
) -> dict:
    if absolute is None:
        absolute = current - previous
    if pct is None and previous:
        pct = round(absolute / previous * 100, 2)
    return {
        "tool": "trends",
        "data": {
            "metric": metric,
            "granularity": "month",
            "timezone": "Asia/Kolkata",
            "current_period": {"start": "c", "end": "d", "value": current},
            "previous_period": {"start": "a", "end": "b", "value": previous},
            "absolute_change": absolute,
            "percentage_change": pct,
            "change_type": change_type,
        },
    }


def performance_envelope(success=87.0, failure=13.0, volume=100) -> dict:
    return {
        "tool": "payment_performance",
        "period": {},
        "data": {
            "transaction_volume": volume,
            "success_rate": {"value": success, "basis": "transaction_volume"},
            "failure_rate": {"value": failure, "basis": "transaction_volume"},
        },
    }


def refunds_envelope(rate=12.0, amount=25_000, count=2) -> dict:
    return {
        "tool": "refunds",
        "period": {},
        "data": {
            "refund_count": count,
            "refund_amount_minor": amount,
            "refund_rate": {
                "value": rate,
                "basis": "definition_A_refund_amount_over_gross_revenue",
            },
            "gross_revenue_minor": 200_000,
        },
    }


def summary_envelope(currencies) -> dict:
    return {
        "tool": "financial_summary",
        "window": {"timezone": "UTC"},
        "filters": {},
        "data": {"currencies": currencies},
    }


def settlements_envelope(currencies) -> dict:
    return {
        "tool": "settlements",
        "window": {"timezone": "UTC"},
        "filters": {},
        "data": {"currencies": currencies},
    }


def by_type(signals):
    return {signal.signal_type: signal for signal in signals}


def full_results() -> dict:
    """Fresh multi-tool envelope set used by several test classes."""
    return {
        "trends": trend_envelope(),
        "payment_performance": performance_envelope(),
        "refunds": refunds_envelope(),
    }


# --- revenue trend signals ------------------------------------------------------


class TestRevenueSignals:
    def test_revenue_decline_exact_values(self):
        signals = analyze_signals({"trends": trend_envelope()})
        signal = by_type(signals)["REVENUE_DECLINE"]
        assert signal.current_value == 800_000
        assert signal.previous_value == 1_000_000
        assert signal.absolute_change == -200_000
        assert signal.percentage_change == -20.0
        assert signal.direction == "DOWN"
        assert signal.severity == "MEDIUM"  # [20, 50) band, documented ladder
        assert signal.unit == "minor_units"
        assert "-20.0%" in signal.evidence
        assert "decreased by 200000" in signal.evidence

    def test_revenue_decline_severity_ladder(self):
        cases = [(-4.0, None), (-5.0, "LOW"), (-19.99, "LOW"),
                 (-20.0, "MEDIUM"), (-49.9, "MEDIUM"), (-50.0, "CRITICAL")]
        for pct, expected in cases:
            signals = analyze_signals(
                {"trends": trend_envelope(current=100, previous=100, absolute=-abs(pct), pct=pct)}
            )
            got = by_type(signals).get("REVENUE_DECLINE")
            assert (got.severity if got else None) == expected, pct

    def test_revenue_growth_and_cap(self):
        up = analyze_signals({"trends": trend_envelope(current=120, previous=100, absolute=20, pct=20.0)})
        growth = by_type(up)["REVENUE_GROWTH"]
        assert growth.direction == "UP"
        assert growth.severity == "MEDIUM"
        huge = analyze_signals({"trends": trend_envelope(current=300, previous=100, absolute=200, pct=200.0)})
        assert by_type(huge)["REVENUE_GROWTH"].severity == "HIGH"  # capped

    def test_small_growth_below_threshold_is_not_a_signal(self):
        signals = analyze_signals({"trends": trend_envelope(current=104, previous=100, absolute=4, pct=4.0)})
        assert signals == []

    def test_no_change_produces_no_signal(self):
        flat = trend_envelope(current=500, previous=500, absolute=0, pct=0.0)
        flat["data"]["change_type"] = "no_activity"
        assert analyze_signals({"trends": flat}) == []

    def test_zero_previous_value_new_activity(self):
        env = trend_envelope(current=300, previous=0, absolute=300, pct=None,
                             change_type="new_activity")
        signals = analyze_signals({"trends": env})
        signal = by_type(signals)["REVENUE_GROWTH"]
        assert signal.percentage_change is None
        assert signal.previous_value == 0
        assert signal.severity == "LOW"  # visible, not overstated
        assert "undefined" in signal.evidence


# --- payment performance ----------------------------------------------------------


class TestPaymentPerformanceSignals:
    @pytest.mark.parametrize(
        ("rate", "expected"),
        [(96.0, None), (95.0, None), (94.9, "MEDIUM"), (85.0, "HIGH"),
         (84.9, "HIGH"), (70.0, "CRITICAL"), (69.9, "CRITICAL")],
    )
    def test_success_rate_level_boundaries(self, rate, expected):
        signals = analyze_signals({"payment_performance": performance_envelope(success=rate, failure=0.0)})
        signal = by_type(signals).get("PAYMENT_SUCCESS_LOW")
        assert (signal.severity if signal else None) == expected, rate

    @pytest.mark.parametrize(
        ("rate", "expected"),
        [(5.0, None), (5.1, "MEDIUM"), (15.0, "HIGH"), (30.0, "CRITICAL"), (30.1, "CRITICAL")],
    )
    def test_failure_rate_level_boundaries(self, rate, expected):
        signals = analyze_signals({"payment_performance": performance_envelope(success=100 - rate, failure=rate)})
        signal = by_type(signals).get("PAYMENT_FAILURE_HIGH")
        assert (signal.severity if signal else None) == expected, rate

    def test_success_rate_none_is_never_low(self):
        # Zero attempts => undefined rate; must not be read as a bad level.
        env = performance_envelope(success=None, failure=None, volume=0)
        assert analyze_signals({"payment_performance": env}) == []

    def test_failure_count_increase_via_trend(self):
        env = trend_envelope(metric="failed_transactions", current=12, previous=8,
                             absolute=4, pct=50.0)
        signals = analyze_signals({"trends": env})
        assert by_type(signals)["FAILURE_COUNT_INCREASE"].severity == "CRITICAL"

    def test_failure_count_decrease_is_not_a_warning(self):
        env = trend_envelope(metric="failed_transactions", current=4, previous=8,
                             absolute=-4, pct=-50.0)
        assert analyze_signals({"trends": env}) == []

    @pytest.mark.parametrize(("pct", "direction_type"),
                             [(-30.0, "VOLUME_DECLINE"), (10.0, "VOLUME_GROWTH")])
    def test_transaction_volume_change(self, pct, direction_type):
        prev = 1000
        cur = int(prev * (1 + pct / 100))
        env = trend_envelope(metric="transaction_volume", current=cur,
                             previous=prev, absolute=cur - prev, pct=pct)
        signals = analyze_signals({"trends": env})
        assert direction_type in by_type(signals)


# --- adverse vs favorable movement cap ------------------------------------------


class TestAdverseFavorableCap:
    """Documented rule: adverse movements may reach CRITICAL; favorable
    movements cap at HIGH (growth is notable, never harmful)."""

    @pytest.mark.parametrize(
        ("metric", "current", "previous", "pct", "expected_type"),
        [
            # favorable: capped at HIGH even at extreme magnitude
            ("gross_revenue", 400, 100, 300.0, "REVENUE_GROWTH"),
            ("transaction_volume", 500, 100, 400.0, "VOLUME_GROWTH"),
            ("settlement_amount", 600_000, 100_000, 500.0,
             "SETTLEMENT_AMOUNT_INCREASE"),
            ("refund_amount", 100, 1_000, -90.0, "REFUND_AMOUNT_DECREASE"),
        ],
    )
    def test_favorable_movements_cap_at_high(
        self, metric, current, previous, pct, expected_type
    ):
        absolute = current - previous
        env = trend_envelope(metric=metric, current=current, previous=previous,
                             absolute=absolute, pct=pct)
        signal = by_type(analyze_signals({"trends": env}))[expected_type]
        assert signal.severity == "HIGH"

    @pytest.mark.parametrize(
        ("metric", "current", "previous", "pct", "expected_type"),
        [
            ("gross_revenue", 40, 100, -60.0, "REVENUE_DECLINE"),
            ("transaction_volume", 30, 100, -70.0, "VOLUME_DECLINE"),
            ("failed_transactions", 900, 300, 200.0, "FAILURE_COUNT_INCREASE"),
            ("refund_amount", 5_000, 1_000, 400.0, "REFUND_AMOUNT_INCREASE"),
            ("settlement_amount", 200_000, 1_000_000, -80.0,
             "SETTLEMENT_AMOUNT_DECREASE"),
        ],
    )
    def test_adverse_movements_reach_critical(
        self, metric, current, previous, pct, expected_type
    ):
        absolute = current - previous
        env = trend_envelope(metric=metric, current=current, previous=previous,
                             absolute=absolute, pct=pct)
        signal = by_type(analyze_signals({"trends": env}))[expected_type]
        assert signal.severity == "CRITICAL"


# --- refunds ------------------------------------------------------------------------


class TestRefundSignals:
    @pytest.mark.parametrize(
        ("rate", "expected"),
        [(5.0, None), (5.1, "MEDIUM"), (15.0, "HIGH"), (18.0, "HIGH")],
    )
    def test_refund_rate_boundaries(self, rate, expected):
        signals = analyze_signals({"refunds": refunds_envelope(rate=rate)})
        signal = by_type(signals).get("REFUND_RATE_HIGH")
        assert (signal.severity if signal else None) == expected, rate

    def test_unusual_refund_activity_only_at_high(self):
        high = by_type(analyze_signals({"refunds": refunds_envelope(rate=18.0)}))
        assert "UNUSUAL_REFUND_ACTIVITY" in high
        medium = by_type(analyze_signals({"refunds": refunds_envelope(rate=6.0)}))
        assert "UNUSUAL_REFUND_ACTIVITY" not in medium
        assert high["UNUSUAL_REFUND_ACTIVITY"].current_value == 18.0

    def test_refund_rate_none_ignored(self):
        env = refunds_envelope()
        env["data"]["refund_rate"]["value"] = None
        assert analyze_signals({"refunds": env}) == []

    def test_refund_amount_increase_and_decrease(self):
        up = analyze_signals({"trends": trend_envelope(metric="refund_amount", current=1500, previous=1000, absolute=500, pct=50.0)})
        assert by_type(up)["REFUND_AMOUNT_INCREASE"].unit == "minor_units"
        down = analyze_signals({"trends": trend_envelope(metric="refund_amount", current=400, previous=1000, absolute=-600, pct=-60.0)})
        assert by_type(down)["REFUND_AMOUNT_DECREASE"].direction == "DOWN"

    def test_refund_count_increase(self):
        env = trend_envelope(metric="refund_count", current=9, previous=3, absolute=6, pct=200.0)
        assert "REFUND_COUNT_INCREASE" in by_type(analyze_signals({"trends": env}))

    @pytest.mark.parametrize("rate", [0.0, 2.5, 5.0])
    def test_healthy_refund_rate_is_not_a_signal(self, rate):
        assert analyze_signals({"refunds": refunds_envelope(rate=rate)}) == []

    def test_payment_success_improvement_is_never_a_signal(self):
        # Healthy-to-perfect success rates must stay silent: no alarm for
        # good news, and 95.00 exactly is still healthy (closed healthy band).
        for rate in (100.0, 99.9, 96.0, 95.0):
            env = performance_envelope(success=rate, failure=round(100 - rate, 2))
            signals = analyze_signals({"payment_performance": env})
            assert "PAYMENT_SUCCESS_LOW" not in by_type(signals), rate


# --- settlements -----------------------------------------------------------------------


class TestSettlementSignals:
    def test_settlement_gap_exact_values(self):
        tool_results = {
            "settlements": settlements_envelope(
                [{"currency": "INR", "settlement_amount_minor": 700_000}]
            ),
            "revenue": {
                "tool": "revenue",
                "period": {},
                "data": {"currency": "INR", "gross_revenue_minor": 1_000_000},
            },
        }
        signals = analyze_signals(tool_results)
        signal = by_type(signals)["SETTLEMENT_GAP"]
        assert signal.current_value == 700_000
        assert signal.previous_value == 1_000_000
        assert signal.absolute_change == 300_000
        assert signal.percentage_change == 30.0
        assert signal.severity == "HIGH"
        assert "INR" in signal.evidence and "300000" in signal.evidence

    def test_gap_severity_bands_per_currency(self):
        def results(settled, gross=1_000_000, code="INR"):
            return {
                "settlements": settlements_envelope(
                    [{"currency": code, "settlement_amount_minor": settled}]
                ),
                "financial_summary": summary_envelope(
                    [{"currency": code, "gross_amount_minor": gross}]
                ),
            }

        t = DEFAULT_THRESHOLDS
        small = analyze_signals(results(int(1_000_000 * (1 - t.settlement_gap_medium_ratio))))
        assert by_type(small)["SETTLEMENT_GAP"].severity == "MEDIUM"
        critical = analyze_signals(results(int(1_000_000 * (1 - t.settlement_gap_critical_ratio))))
        assert by_type(critical)["SETTLEMENT_GAP"].severity == "CRITICAL"
        none = analyze_signals(results(999_000))
        assert "SETTLEMENT_GAP" not in by_type(none)
        # Exact HIGH boundary: gap ratio == settlement_gap_high_ratio.
        high = analyze_signals(results(int(1_000_000 * (1 - t.settlement_gap_high_ratio))))
        assert by_type(high)["SETTLEMENT_GAP"].severity == "HIGH"

    def test_gap_skips_unknown_or_zero_gross_currencies(self):
        tool_results = {
            "settlements": settlements_envelope([
                {"currency": "USD", "settlement_amount_minor": 5},
                {"currency": "INR", "settlement_amount_minor": 0},
            ]),
            "financial_summary": summary_envelope(
                [{"currency": "INR", "gross_amount_minor": 0}]
            ),
        }
        assert analyze_signals(tool_results) == []

    def test_settlement_trend_change(self):
        env = trend_envelope(metric="settlement_amount", current=900_000,
                             previous=1_200_000, absolute=-300_000, pct=-25.0)
        signal = by_type(analyze_signals({"trends": env}))["SETTLEMENT_AMOUNT_DECREASE"]
        assert signal.unit == "minor_units"


# --- multi-metric coincidence ---------------------------------------------------------


class TestCoincidence:
    def test_multiple_simultaneous_signals_from_full_results(self):
        """One run over trends + performance + refunds yields every expected
        finding at once, including the coincidence signal, with no dupes."""
        signals = analyze_signals(full_results())
        types = [signal.signal_type for signal in signals]
        assert set(types) == {
            "REVENUE_DECLINE",           # -20% trend
            "PAYMENT_SUCCESS_LOW",       # 87% < 95%
            "PAYMENT_FAILURE_HIGH",      # 13% > 5%
            "REFUND_RATE_HIGH",          # 12% > 5%
            "REVENUE_PAYMENT_COINCIDENCE",
        }
        assert len(types) == len(set(types))
        by_type_map = by_type(signals)
        assert by_type_map["REVENUE_DECLINE"].severity == "MEDIUM"
        assert by_type_map["PAYMENT_SUCCESS_LOW"].severity == "MEDIUM"
        assert by_type_map["PAYMENT_FAILURE_HIGH"].severity == "MEDIUM"  # 13 ∈ (5, 15)
        assert by_type_map["REFUND_RATE_HIGH"].severity == "MEDIUM"

    def test_revenue_decline_plus_payment_issue_coincide(self):
        tool_results = {
            "trends": trend_envelope(),
            "payment_performance": performance_envelope(success=87.0, failure=13.0),
        }
        signals = analyze_signals(tool_results)
        coincidence = by_type(signals)["REVENUE_PAYMENT_COINCIDENCE"]
        assert coincidence.severity == "INFO"
        assert "coincides" in coincidence.evidence
        assert "not cause" in coincidence.evidence

    def test_no_causation_wording_anywhere(self):
        tool_results = {
            "trends": trend_envelope(),
            "payment_performance": performance_envelope(success=80.0, failure=20.0),
        }
        text = json.dumps([s.model_dump(mode="json")
                           for s in analyze_signals(tool_results)])
        for banned in ("caused by", "because customers", "due to customers"):
            assert banned not in text.lower()

    def test_revenue_decline_alone_has_no_coincidence(self):
        assert "REVENUE_PAYMENT_COINCIDENCE" not in by_type(
            analyze_signals({"trends": trend_envelope()})
        )

    def test_payment_issue_alone_has_no_coincidence(self):
        assert "REVENUE_PAYMENT_COINCIDENCE" not in by_type(
            analyze_signals({"payment_performance": performance_envelope()})
        )


# --- dedup, determinism, serialization -----------------------------------------------


class TestDeterminismAndDedup:
    def test_duplicate_keys_are_collapsed(self):
        signals = SignalAnalyzer().analyze(full_results())
        keys = [signal.key for signal in signals]
        assert len(keys) == len(set(keys))

    def test_insertion_order_does_not_matter(self):
        reversed_results = dict(reversed(list(full_results().items())))
        first = [s.model_dump(mode="json") for s in SignalAnalyzer().analyze(full_results())]
        second = [s.model_dump(mode="json") for s in SignalAnalyzer().analyze(reversed_results)]
        assert first == second

    def test_repeated_runs_are_identical(self):
        analyzer = SignalAnalyzer()
        assert ([s.model_dump(mode="json") for s in analyzer.analyze(full_results())]
                == [s.model_dump(mode="json") for s in analyzer.analyze(full_results())])

    def test_output_is_json_serializable(self):
        encoded = json.dumps(
            [s.model_dump(mode="json") for s in analyze_signals(full_results())]
        )
        decoded = json.loads(encoded)
        assert isinstance(decoded, list) and decoded


# --- missing values & robustness ----------------------------------------------------


class TestMissingValues:
    def test_empty_tool_results(self):
        assert analyze_signals({}) == []

    def test_malformed_envelopes_are_skipped(self):
        junk = {
            "revenue": "not-a-dict",
            "payment_performance": {"data": None},
            "refunds": {"tool": "refunds"},          # no data key
            "unknown_tool": {"data": {"x": 1}},
        }
        assert analyze_signals(junk) == []

    def test_trend_with_missing_metric_is_ignored(self):
        env = trend_envelope()
        env["data"]["metric"] = "profit_margin"  # not an engine metric
        assert analyze_signals({"trends": env}) == []

    def test_non_engine_trend_metric_never_gets_signal_vocabulary(self):
        env = trend_envelope()
        env["data"]["metric"] = "customer_churn"
        assert analyze_signals({"trends": env}) == []


# --- configurability ------------------------------------------------------------------


class TestConfigurability:
    def test_custom_thresholds_change_outcome(self):
        strict = SignalThresholds(change_low_pct=1.0, change_medium_pct=2.0,
                                  change_high_pct=3.0)
        relaxed = SignalThresholds(change_low_pct=90.0)
        env = trend_envelope(current=95, previous=100, absolute=-5, pct=-5.0)
        strict_signals = by_type(SignalAnalyzer(strict).analyze({"trends": deepcopy(env)}))
        relaxed_signals = by_type(SignalAnalyzer(relaxed).analyze({"trends": deepcopy(env)}))
        # Documented ladder: |Δ| >= high_pct reaches the top band, and a
        # revenue decline is adverse -> CRITICAL. With high=3 the same -5%
        # that is only LOW under defaults has crossed every boundary.
        assert strict_signals["REVENUE_DECLINE"].severity == "CRITICAL"
        assert "REVENUE_DECLINE" not in relaxed_signals

    def test_medium_band_respects_custom_boundaries(self):
        custom = SignalThresholds(change_low_pct=1.0, change_medium_pct=4.0,
                                  change_high_pct=10.0)
        env = trend_envelope(current=95, previous=100, absolute=-5, pct=-5.0)
        signal = by_type(SignalAnalyzer(custom).analyze({"trends": env}))["REVENUE_DECLINE"]
        assert signal.severity == "MEDIUM"  # 4 <= 5 < 10


# --- purity ------------------------------------------------------------------------------


class TestPurity:
    def test_module_has_no_llm_db_or_network_imports(self):
        import inspect

        import app.services.signal_analysis as module

        source = inspect.getsource(module)
        for forbidden in ("openai", "LLMService", "sqlalchemy", "Session",
                          "create_engine", "requests", "httpx", "os.environ",
                          "Database"):
            assert forbidden not in source, forbidden

    def test_input_mapping_is_not_mutated(self):
        tool_results = full_results()
        snapshot = deepcopy(tool_results)
        analyze_signals(tool_results)
        assert tool_results == snapshot


def test_financial_signal_model_roundtrip():
    signal = FinancialSignal(
        key="X:y", signal_type="REVENUE_DECLINE", metric="gross_revenue",
        severity="LOW", unit="minor_units", direction="DOWN",
        current_value=1, previous_value=2, absolute_change=-1,
        percentage_change=-50.0, evidence="e",
    )
    assert FinancialSignal.model_validate_json(signal.model_dump_json()) == signal

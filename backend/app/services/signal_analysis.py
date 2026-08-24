"""Financial Signal Analysis Engine — deterministic signal detection.

DETECT / CLASSIFY layer of the architecture:

    Finance Engine (CALCULATE) -> metrics -> Signal Analyzer (DETECT)
        -> Financial Signals -> LangGraph (ORCHESTRATE) -> LLM (EXPLAIN)

Hard boundaries:

- **No LLM.** This module is pure, importable-by-anything Python.
- **No database.** Input is already-computed structured tool envelopes
  (the same dicts the Finance Tools return); output is a list of
  :class:`~app.schemas.signals.FinancialSignal` models.
- **No new financial math beyond change framing.** Every number in a
  signal is either an engine value echoed verbatim or the engine's own
  absolute/percentage change. Level checks compare an engine value
  against configured thresholds; nothing is recomputed differently from
  ``FinanceService``.

Thresholds: docs/FINANCE_METRICS_SPECIFICATION.md defines **no** alert
or anomaly thresholds — only metric formulas and status-enum logging.
Every threshold below is therefore a conservative, configurable project
assumption (not from Razorpay or any external authority) and is injected
via :class:`SignalThresholds` so it can be tuned per deployment without
code changes. Boundary semantics are documented on that class: escalation
is inclusive (a value exactly ON a boundary takes the more severe band),
with one exception — a rate exactly at its healthy limit stays healthy
(95.00% success, 5.00% failure, 5.00% refund). Adverse movements may reach
CRITICAL; favorable ones cap at HIGH.

Facts, never causes: evidence sentences state what changed and by how
much ("Gross revenue decreased by ... (-20.0%) ..."), never why. The one
multi-metric signal (REVENUE_PAYMENT_COINCIDENCE) uses coincidence
wording only — "coincides with", never "caused by".
"""

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.schemas.signals import FinancialSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignalThresholds:
    """Configurable detection thresholds (project assumptions).

    Percentages are percentage points as produced by the engine (2-decimal
    floats). Rate levels mirror them; all values are conservative defaults
    chosen for a payments business and are safe to tune.

    Boundary semantics (uniform rule, one documented exception):

    - **Escalation is inclusive.** A value exactly ON any band boundary
      belongs to the MORE SEVERE band: ``|change| == 5`` is a LOW signal,
      ``|change| == 50`` reaches the top band, ``failure_rate == 15`` is
      HIGH, ``success_rate == 85`` is HIGH, ``gap_ratio == 0.30`` is HIGH.
    - **Exception — closed healthy bands.** A rate sitting exactly on its
      healthy limit is still healthy: ``success_rate == 95`` produces no
      signal, ``failure_rate == 5`` and ``refund_rate == 5`` likewise.
      Exactly meeting the minimum standard is meeting it.
    - **Change and gap metrics have no healthy band** (their perfect value
      is zero), so their reporting floors are inclusive too: a gap ratio of
      exactly ``settlement_gap_medium_ratio`` yields a MEDIUM signal.

    Severity ladder for change signals (on ``|percentage_change|``):

        |Δ| < low            -> no signal
        low  <= |Δ| < medium -> LOW
        medium<= |Δ| < high  -> MEDIUM
        high <= |Δ|          -> CRITICAL if the movement is adverse,
                                HIGH if it is favorable (growth is
                                notable, never harmful)
    """

    # Change signals (|percentage_change| bands; see the class docstring
    # for the exact inclusive boundaries).
    change_low_pct: float = 5.0
    change_medium_pct: float = 20.0
    change_high_pct: float = 50.0

    # Payment success rate level checks (engine %, None = undefined).
    # Healthy: value >= medium_below (95.00 itself is healthy).
    # MEDIUM: medium_below > value > high_below;
    # HIGH: high_below >= value > critical_below (85.00 is HIGH);
    # CRITICAL: value <= critical_below (70.00 is CRITICAL).
    success_rate_medium_below_pct: float = 95.0
    success_rate_high_below_pct: float = 85.0
    success_rate_critical_below_pct: float = 70.0

    # Payment failure rate level checks. Healthy <= medium_above;
    # [high_above] inclusive escalation above that.
    failure_rate_medium_above_pct: float = 5.0
    failure_rate_high_above_pct: float = 15.0
    failure_rate_critical_above_pct: float = 30.0

    # Refund-rate level checks (% of gross revenue). Healthy <= medium.
    refund_rate_medium_above_pct: float = 5.0
    refund_rate_high_above_pct: float = 15.0

    # Settlement gap: (gross − settled) / gross per currency. Inclusive
    # escalation at every edge including the MEDIUM floor.
    settlement_gap_medium_ratio: float = 0.10
    settlement_gap_high_ratio: float = 0.30
    settlement_gap_critical_ratio: float = 0.60


DEFAULT_THRESHOLDS = SignalThresholds()

#: Trend metric names (keys of FinanceService.TREND_METRIC_EXTRACTORS)
#: mapped to the signal family they feed. Metrics absent here produce no
#: trend signal — the analyzer never invents vocabulary.
_TREND_SIGNAL_METRICS = {
    "gross_revenue": "revenue",
    "transaction_volume": "volume",
    "failed_transactions": "failure_count",
    "refund_amount": "refund_amount",
    "refund_count": "refund_count",
    "settlement_amount": "settlement_amount",
}

#: The movement direction that is ADVERSE (potentially harmful) per trend
#: family. Adverse changes may reach CRITICAL; favorable ones cap at HIGH.
_ADVERSE_DIRECTION = {
    "revenue": "DOWN",
    "volume": "DOWN",
    "failure_count": "UP",
    "refund_amount": "UP",
    "refund_count": "UP",
    "settlement_amount": "DOWN",
}


class SignalAnalyzer:
    """Deterministic detector over Finance Tool envelopes."""

    def __init__(self, thresholds: SignalThresholds = DEFAULT_THRESHOLDS) -> None:
        self._thresholds = thresholds

    # --- public API ---------------------------------------------------------

    def analyze(self, tool_results: Mapping[str, Any]) -> list[FinancialSignal]:
        """Detect signals from keyed tool envelopes (name -> envelope).

        Deterministic: same input mapping (any iteration order) yields the
        exact same signal list. Unknown tool names and malformed envelopes
        are skipped silently at debug level — missing data can never
        become a fabricated finding.
        """
        signals: list[FinancialSignal] = []

        revenue_envelope = self._envelope(tool_results, "revenue")
        performance_envelope = self._envelope(tool_results, "payment_performance")
        refunds_envelope = self._envelope(tool_results, "refunds")
        summary_envelope = self._envelope(tool_results, "financial_summary")
        settlements_envelope = self._envelope(tool_results, "settlements")
        trend_envelopes = {
            name: envelope
            for name, envelope in tool_results.items()
            if isinstance(envelope, dict)
            and isinstance(envelope.get("data"), dict)
            and "granularity" in envelope["data"]
            and envelope["data"].get("metric") in _TREND_SIGNAL_METRICS
        }

        for envelope in trend_envelopes.values():
            signals.extend(self._trend_signals(envelope))
        signals.extend(self._success_level_signals(performance_envelope))
        signals.extend(self._failure_level_signals(performance_envelope))
        signals.extend(self._refund_level_signals(refunds_envelope))
        signals.extend(
            self._settlement_gap_signals(
                settlements_envelope,
                summary_envelope or self._summary_view(revenue_envelope),
            )
        )
        signals.extend(self._coincidence_signals(signals))

        # Deduplicate by stable key, preserving first-seen (deterministic) order.
        unique: dict[str, FinancialSignal] = {}
        for signal in signals:
            unique.setdefault(signal.key, signal)
        result = list(unique.values())
        logger.info("Signal analysis detected %d signal(s)", len(result))
        return result

    # --- internals ------------------------------------------------------------

    @staticmethod
    def _envelope(tool_results: Mapping[str, Any], name: str) -> dict[str, Any] | None:
        envelope = tool_results.get(name)
        if isinstance(envelope, dict) and isinstance(envelope.get("data"), dict):
            return envelope
        return None

    def _trend_signals(self, envelope: Mapping[str, Any]) -> list[FinancialSignal]:
        """Change signals from one trends envelope (engine Part 6 output)."""
        data = envelope["data"]
        metric_name = data.get("metric")
        family = _TREND_SIGNAL_METRICS.get(metric_name)
        if family is None:
            return []
        current = data.get("current_period") or {}
        previous = data.get("previous_period") or {}
        current_value = current.get("value")
        previous_value = previous.get("value")
        absolute = data.get("absolute_change")
        pct = data.get("percentage_change")
        change_type = data.get("change_type")

        if current_value is None:
            return []
        if absolute == 0 or change_type == "no_activity":
            return []  # no-change metrics must not produce noise signals

        direction = "UP" if (absolute or 0) > 0 else "DOWN"
        unit = "count"
        if family == "revenue":
            signal_type = (
                "REVENUE_GROWTH" if direction == "UP" else "REVENUE_DECLINE"
            )
            label = "Gross revenue"
            unit = "minor_units"
        elif family == "volume":
            signal_type = (
                "VOLUME_GROWTH" if direction == "UP" else "VOLUME_DECLINE"
            )
            label = "Transaction volume"
        elif family == "failure_count":
            if direction != "UP":
                return []  # fewer failures is good news, not a warning
            signal_type = "FAILURE_COUNT_INCREASE"
            label = "Failed transactions"
        elif family == "refund_amount":
            signal_type = (
                "REFUND_AMOUNT_INCREASE"
                if direction == "UP"
                else "REFUND_AMOUNT_DECREASE"
            )
            label = "Refund amount"
            unit = "minor_units"
        elif family == "refund_count":
            if direction != "UP":
                return []
            signal_type = "REFUND_COUNT_INCREASE"
            label = "Refund count"
        else:  # settlement_amount
            signal_type = (
                "SETTLEMENT_AMOUNT_INCREASE"
                if direction == "UP"
                else "SETTLEMENT_AMOUNT_DECREASE"
            )
            label = "Settlement amount"
            unit = "minor_units"

        severity = self._severity_for_change(
            direction,
            pct,
            adverse=direction == _ADVERSE_DIRECTION.get(family),
        )
        if severity is None:
            return []

        movement = "increased" if direction == "UP" else "decreased"
        magnitude = abs(absolute) if absolute is not None else 0
        unit_words = unit.replace("_", " ")
        if pct is None:  # previous value was zero (spec Part 6.4 semantics)
            evidence = (
                f"{label} {movement} by {magnitude} {unit_words} compared "
                "with the previous period (percentage change is undefined "
                "because the previous period had no activity)."
            )
        else:
            evidence = (
                f"{label} {movement} by {magnitude} {unit_words} ({pct}%) "
                "compared with the previous period."
            )
        key_suffix = f":{metric_name}"
        return [
            FinancialSignal(
                key=f"{signal_type}{key_suffix}",
                signal_type=signal_type,
                metric=str(metric_name),
                severity=severity,
                unit=unit,  # type: ignore[arg-type]
                direction=direction,  # type: ignore[arg-type]
                current_value=current_value,
                previous_value=previous_value,
                absolute_change=absolute,
                percentage_change=pct,
                evidence=evidence,
            )
        ]

    def _severity_for_change(
        self, direction: str, pct: float | None, *, adverse: bool
    ) -> str | None:
        """LOW/MEDIUM/HIGH/CRITICAL banding for a signed change.

        Inclusive escalation: ``|pct|`` equal to a boundary takes the more
        severe band. Adverse movements may reach CRITICAL; favorable ones
        cap at HIGH (growth is notable, never harmful). An undefined
        percentage (previous period had no activity) is reported factually
        at LOW so the event stays visible without being overstated.
        """
        if pct is None:
            return "LOW"
        magnitude = abs(pct)
        t = self._thresholds
        if magnitude < t.change_low_pct:
            return None
        if magnitude < t.change_medium_pct:
            return "LOW"
        if magnitude < t.change_high_pct:
            return "MEDIUM"
        if adverse:
            return "CRITICAL"
        return "HIGH"

    def _success_level_signals(
        self, envelope: Mapping[str, Any] | None
    ) -> list[FinancialSignal]:
        if envelope is None:
            return []
        data = envelope["data"]
        rate_block = data.get("success_rate") or {}
        value = rate_block.get("value")
        if value is None:  # undefined rate (zero attempts) is not "low"
            return []
        t = self._thresholds
        if value >= t.success_rate_medium_below_pct:
            return []  # exactly 95.00 is healthy: meeting the bar is meeting it
        if value <= t.success_rate_critical_below_pct:
            severity = "CRITICAL"
        elif value <= t.success_rate_high_below_pct:
            severity = "HIGH"
        else:
            severity = "MEDIUM"
        return [
            FinancialSignal(
                key="PAYMENT_SUCCESS_LOW:success_rate",
                signal_type="PAYMENT_SUCCESS_LOW",
                metric="success_rate",
                severity=severity,  # type: ignore[arg-type]
                unit="percent",
                current_value=value,
                evidence=(
                    f"Payment success rate is {value}% in the analyzed "
                    f"period, below the configured review threshold of "
                    f"{t.success_rate_medium_below_pct}%."
                ),
            )
        ]

    def _failure_level_signals(
        self, envelope: Mapping[str, Any] | None
    ) -> list[FinancialSignal]:
        if envelope is None:
            return []
        data = envelope["data"]
        rate_block = data.get("failure_rate") or {}
        value = rate_block.get("value")
        if value is None:
            return []
        t = self._thresholds
        if value <= t.failure_rate_medium_above_pct:
            return []  # exactly 5.00 is healthy: meeting the bar is meeting it
        if value >= t.failure_rate_critical_above_pct:
            severity = "CRITICAL"
        elif value >= t.failure_rate_high_above_pct:
            severity = "HIGH"
        else:
            severity = "MEDIUM"
        return [
            FinancialSignal(
                key="PAYMENT_FAILURE_HIGH:failure_rate",
                signal_type="PAYMENT_FAILURE_HIGH",
                metric="failure_rate",
                severity=severity,  # type: ignore[arg-type]
                unit="percent",
                current_value=value,
                evidence=(
                    f"Payment failure rate is {value}% in the analyzed "
                    f"period, above the configured review threshold of "
                    f"{t.failure_rate_medium_above_pct}%."
                ),
            )
        ]

    def _refund_level_signals(
        self, envelope: Mapping[str, Any] | None
    ) -> list[FinancialSignal]:
        if envelope is None:
            return []
        data = envelope["data"]
        rate_block = data.get("refund_rate") or {}
        value = rate_block.get("value")
        signals: list[FinancialSignal] = []
        if value is not None:
            t = self._thresholds
            if value <= t.refund_rate_medium_above_pct:
                severity = None  # exactly 5.00 is healthy
            elif value >= t.refund_rate_high_above_pct:
                severity = "HIGH"
            else:
                severity = "MEDIUM"
            if severity is not None:
                signals.append(
                    FinancialSignal(
                        key="REFUND_RATE_HIGH:refund_rate",
                        signal_type="REFUND_RATE_HIGH",
                        metric="refund_rate",
                        severity=severity,  # type: ignore[arg-type]
                        unit="percent",
                        current_value=value,
                        evidence=(
                            f"The refund rate is {value}% of gross revenue "
                            f"in the analyzed period, above the configured "
                            f"review threshold of "
                            f"{t.refund_rate_medium_above_pct}%."
                        ),
                    )
                )
                if severity == "HIGH":
                    signals.append(
                        FinancialSignal(
                            key="UNUSUAL_REFUND_ACTIVITY:refunds",
                            signal_type="UNUSUAL_REFUND_ACTIVITY",
                            metric="refunds",
                            severity="HIGH",
                            unit="percent",
                            current_value=value,
                            evidence=(
                                f"A refund rate of {value}% of gross "
                                "revenue exceeds the configured high "
                                "review threshold; treat as unusual until "
                                "reviewed against known refunds."
                            ),
                        )
                    )
        return signals

    def _settlement_gap_signals(
        self,
        settlements_envelope: Mapping[str, Any] | None,
        gross_source: Mapping[str, Any] | None,
    ) -> list[FinancialSignal]:
        """Gap between settled amounts and gross revenue, per currency.

        The gap is derived by subtracting two engine-provided totals for
        the SAME currency — no rate is recomputed and no currency mixing
        occurs (spec Part 9).
        """
        if settlements_envelope is None or gross_source is None:
            return []
        gross_by_currency: dict[str, int] = {}
        source_data = gross_source.get("data") or {}
        for item in source_data.get("currencies") or []:
            code = item.get("currency")
            gross = item.get("gross_amount_minor")
            if isinstance(code, str) and isinstance(gross, int):
                gross_by_currency[code] = gross

        signals: list[FinancialSignal] = []
        t = self._thresholds
        for item in (settlements_envelope.get("data") or {}).get(
            "currencies"
        ) or []:
            code = item.get("currency")
            settled = item.get("settlement_amount_minor")
            if not isinstance(code, str) or not isinstance(settled, int):
                continue
            gross = gross_by_currency.get(code)
            if gross is None or gross <= 0:
                continue
            gap = gross - settled
            ratio = gap / gross
            if ratio < t.settlement_gap_medium_ratio:
                continue
            if ratio >= t.settlement_gap_critical_ratio:
                severity = "CRITICAL"
            elif ratio >= t.settlement_gap_high_ratio:
                severity = "HIGH"
            else:
                severity = "MEDIUM"
            pct = round(ratio * 100, 2)
            signals.append(
                FinancialSignal(
                    key=f"SETTLEMENT_GAP:{code}",
                    signal_type="SETTLEMENT_GAP",
                    metric=f"settlements:{code}",
                    severity=severity,  # type: ignore[arg-type]
                    unit="minor_units",
                    current_value=settled,
                    previous_value=gross,
                    absolute_change=gap,
                    percentage_change=pct,
                    evidence=(
                        f"In {code}, settlements total {settled} minor units "
                        f"against gross revenue of {gross} minor units in "
                        f"the analyzed window; {gap} minor units "
                        f"({pct}% of gross) had not appeared as settlements."
                    ),
                )
            )
        return signals

    def _summary_view(
        self, revenue_envelope: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        """Adapt a single-currency revenue envelope to gap-check input."""
        if revenue_envelope is None:
            return None
        data = revenue_envelope["data"]
        return {
            "data": {
                "currencies": [
                    {
                        "currency": data.get("currency"),
                        "gross_amount_minor": data.get("gross_revenue_minor"),
                    }
                ]
            }
        }

    def _coincidence_signals(
        self, signals: Iterable[FinancialSignal]
    ) -> list[FinancialSignal]:
        """Co-occurrence wording between revenue decline and payment issues.

        Deliberately NOT causal: the evidence says "coincides with".
        """
        types = {signal.signal_type for signal in signals}
        revenue_down = "REVENUE_DECLINE" in types
        payment_issue = bool(types & {"PAYMENT_SUCCESS_LOW", "PAYMENT_FAILURE_HIGH"})
        if not (revenue_down and payment_issue):
            return []
        return [
            FinancialSignal(
                key="REVENUE_PAYMENT_COINCIDENCE:revenue+payments",
                signal_type="REVENUE_PAYMENT_COINCIDENCE",
                metric="revenue+payments",
                severity="INFO",
                unit="percent",
                evidence=(
                    "The observed revenue decline coincides with degraded "
                    "payment performance in the same window. The available "
                    "data shows co-occurrence, not cause."
                ),
            )
        ]


def analyze_signals(
    tool_results: Mapping[str, Any],
    *,
    thresholds: SignalThresholds = DEFAULT_THRESHOLDS,
) -> list[FinancialSignal]:
    """Module-level convenience wrapper around :class:`SignalAnalyzer`."""
    return SignalAnalyzer(thresholds).analyze(tool_results)

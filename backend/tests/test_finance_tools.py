"""Finance Tools contract tests (fully mocked FinanceService).

No database, no provider, no LLM. Verifies for every tool:

- delegation: exactly one FinanceService method is called, with the exact
  parameters derived from validated input;
- fidelity: engine responses are echoed verbatim — including deliberately
  *inconsistent* numbers, proving tools add no arithmetic;
- safety: outputs are plain JSON-native dicts; errors are typed,
  machine-readable, and free of SQL/DSN/credential material;
- validation: Literal choices stay in sync with app.core.periods and
  app.services.metrics vocabularies.
"""

import json
from datetime import date, datetime
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.ai.tools import (
    ALL_FINANCE_TOOLS,
    DEFAULT_TOOL_CURRENCY,
    FINANCIAL_SUMMARY_TOOL,
    PAYMENT_PERFORMANCE_TOOL,
    REVENUE_TOOL,
    SETTLEMENT_TOOL,
    TREND_TOOL,
    REFUND_TOOL,
    FinanceEngineError,
    InvalidToolInputError,
    SettlementToolInput,
    TrendToolInput,
    UnknownToolError,
    get_finance_tool,
)
from app.ai.tools import (
    payments as payments_module,
    refunds as refunds_module,
    revenue as revenue_module,
    settlements as settlements_module,
    summary as summary_module,
    trends as trends_module,
)
from app.ai.tools.inputs import (
    FinancialSummaryToolInput,
    GranularityName,
    PaymentPerformanceToolInput,
    PeriodName,
    RefundToolInput,
    RevenueToolInput,
    TrendMetricName,
)
from app.api.finance import DEFAULT_METRIC_CURRENCY
from app.core.periods import (
    PERIOD_PRESETS,
    REPORTING_TIMEZONE,
    TREND_GRANULARITIES,
)
from app.db.session import DatabaseNotConfiguredError
from app.schemas.finance import (
    FinanceCurrencySummary,
    FinanceSummaryResponse,
    MetricRate,
    NetRevenueVariants,
    PaymentPerformanceResponse,
    RefundMetricsBlock,
    ReportingPeriod,
    RevenueMetricsResponse,
    TrendResponse,
    TrendWindow,
)
from app.services.metrics import TREND_METRIC_EXTRACTORS

SESSION = object()  # sentinel; tools must pass it through untouched

TOOL_MODULES = (
    revenue_module,
    payments_module,
    refunds_module,
    settlements_module,
    trends_module,
    summary_module,
)


# --- stubs -------------------------------------------------------------------


class RecordingFinanceService:
    """Stands in for FinanceService: records calls, replays results."""

    def __init__(
        self,
        results: dict[str, Any] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._results = results or {}
        self._errors = errors or {}
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def _call(self, name: str, session: Any, kwargs: dict[str, Any]) -> Any:
        self.calls.append((name, session, kwargs))
        if name in self._errors:
            raise self._errors[name]
        return self._results[name]

    def revenue(self, session, **kwargs):
        return self._call("revenue", session, kwargs)

    def payment_performance(self, session, **kwargs):
        return self._call("payment_performance", session, kwargs)

    def trend(self, session, **kwargs):
        return self._call("trend", session, kwargs)

    def summary(self, session, **kwargs):
        return self._call("summary", session, kwargs)


@pytest.fixture
def install(monkeypatch):
    """Patch every tool module's FinanceService binding with ``recorder``."""

    def _install(recorder: RecordingFinanceService) -> RecordingFinanceService:
        for module in TOOL_MODULES:
            monkeypatch.setattr(module, "FinanceService", recorder)
        return recorder

    return _install


# --- canned engine responses --------------------------------------------------


def make_period() -> ReportingPeriod:
    return ReportingPeriod(
        start=datetime(2026, 3, 11, 0, 0, tzinfo=REPORTING_TIMEZONE),
        end=datetime(2026, 3, 11, 14, 30, tzinfo=REPORTING_TIMEZONE),
    )


def make_revenue_response() -> RevenueMetricsResponse:
    return RevenueMetricsResponse(
        period=make_period(),
        currency="INR",
        gross_revenue_minor=1_200_000,
        razorpay_fee_minor=11_760,
        razorpay_tax_minor=1_824,
        net_revenue=NetRevenueVariants(
            net_of_refunds_minor=1_197_500,
            net_of_fees_minor=1_186_416,
            net_of_refunds_and_fees_minor=1_183_916,
        ),
        refunds=RefundMetricsBlock(
            refund_count=2,
            refund_amount_minor=2_500,
            refund_rate=MetricRate(
                value=0.21, basis="definition_A_refund_amount_over_gross_revenue"
            ),
        ),
        other_currency_transactions_excluded=3,
    )


def make_performance_response() -> PaymentPerformanceResponse:
    return PaymentPerformanceResponse(
        period=make_period(),
        currency="INR",
        transaction_volume=120,
        successful_transactions=110,
        failed_transactions=6,
        in_progress_transactions=4,
        success_rate=MetricRate(value=91.67, basis="transaction_volume"),
        failure_rate=MetricRate(value=5.0, basis="transaction_volume"),
        other_currency_transactions_excluded=3,
    )


def make_trend_response() -> TrendResponse:
    return TrendResponse(
        metric="gross_revenue",
        granularity="day",
        timezone="Asia/Kolkata",
        current_period=TrendWindow(
            start=datetime(2026, 3, 11, 0, 0, tzinfo=REPORTING_TIMEZONE),
            end=datetime(2026, 3, 11, 14, 30, tzinfo=REPORTING_TIMEZONE),
            value=1_200_000,
        ),
        previous_period=TrendWindow(
            start=datetime(2026, 3, 10, 0, 0, tzinfo=REPORTING_TIMEZONE),
            end=datetime(2026, 3, 11, 0, 0, tzinfo=REPORTING_TIMEZONE),
            value=1_000_000,
        ),
        absolute_change=200_000,
        percentage_change=20.0,
        change_type="normal",
    )


def make_currency_summary() -> FinanceCurrencySummary:
    return FinanceCurrencySummary(
        currency="INR",
        gross_amount_minor=1_200_000,
        transaction_count=120,
        successful_amount_minor=1_200_000,
        successful_count=110,
        failed_count=6,
        in_progress_count=4,
        fee_minor=11_760,
        tax_minor=1_824,
        refunded_amount_minor=2_500,
        refund_count=2,
        net_of_refunds_minor=1_197_500,
        net_of_fees_minor=1_186_416,
        net_of_refunds_and_fees_minor=1_183_916,
        settlement_amount_minor=950_000,
        settlement_fees_minor=10_100,
        settlement_tax_minor=1_700,
    )


def make_summary_response(**overrides: Any) -> FinanceSummaryResponse:
    fields: dict[str, Any] = {
        "start_date": date(2026, 3, 1),
        "end_date": date(2026, 3, 11),
        "timezone": "UTC",
        "currency": None,
        "status": None,
        "method": None,
        "currencies": [make_currency_summary()],
    }
    fields.update(overrides)
    return FinanceSummaryResponse(**fields)


def empty_results() -> dict[str, Any]:
    """Zero-degraded engine responses (empty dataset semantics)."""
    zero_period = make_period()
    zero_rates = MetricRate(value=None, basis="transaction_volume")
    return {
        "revenue": RevenueMetricsResponse(
            period=zero_period,
            currency="INR",
            gross_revenue_minor=0,
            razorpay_fee_minor=0,
            razorpay_tax_minor=0,
            net_revenue=NetRevenueVariants(
                net_of_refunds_minor=0,
                net_of_fees_minor=0,
                net_of_refunds_and_fees_minor=0,
            ),
            refunds=RefundMetricsBlock(
                refund_count=0,
                refund_amount_minor=0,
                refund_rate=MetricRate(value=None, basis="x"),
            ),
            other_currency_transactions_excluded=0,
        ),
        "payment_performance": PaymentPerformanceResponse(
            period=zero_period,
            currency="INR",
            transaction_volume=0,
            successful_transactions=0,
            failed_transactions=0,
            in_progress_transactions=0,
            success_rate=zero_rates,
            failure_rate=zero_rates,
            other_currency_transactions_excluded=0,
        ),
        "trend": TrendResponse(
            metric="refund_count",
            granularity="week",
            timezone="Asia/Kolkata",
            current_period=TrendWindow(
                start=zero_period.start, end=zero_period.end, value=0
            ),
            previous_period=TrendWindow(
                start=zero_period.start, end=zero_period.end, value=0
            ),
            absolute_change=0,
            percentage_change=None,
            change_type="no_activity",
        ),
        "summary": FinanceSummaryResponse(currencies=[]),
    }


# --- vocabulary drift guards ----------------------------------------------------


class TestVocabularySync:
    def test_period_names_match_engine_presets(self):
        assert set(get_args(PeriodName)) == set(PERIOD_PRESETS)

    def test_granularities_match_engine_presets(self):
        assert set(get_args(GranularityName)) == set(TREND_GRANULARITIES)

    def test_trend_metrics_match_engine_extractors(self):
        assert set(get_args(TrendMetricName)) == set(TREND_METRIC_EXTRACTORS)

    def test_default_currency_matches_finance_api(self):
        assert DEFAULT_TOOL_CURRENCY == DEFAULT_METRIC_CURRENCY


# --- registry -----------------------------------------------------------------


class TestRegistry:
    def test_seven_tools_with_unique_stable_names(self):
        assert len(ALL_FINANCE_TOOLS) == 7
        names = [tool.name for tool in ALL_FINANCE_TOOLS]
        assert len(set(names)) == len(names)
        assert set(names) == {
            "revenue",
            "payment_performance",
            "refunds",
            "settlements",
            "trends",
            "financial_summary",
            "reconcile_transactions",
        }

    def test_lookup_roundtrip_and_metadata(self):
        for tool in ALL_FINANCE_TOOLS:
            found = get_finance_tool(tool.name)
            assert found is tool
            assert tool.description
            assert issubclass(tool.input_model, object)

    def test_unknown_tool_is_typed_error(self):
        with pytest.raises(UnknownToolError) as excinfo:
            get_finance_tool("make_money")
        assert excinfo.value.code == "unknown_tool"
        assert "available" in str(excinfo.value)


# --- revenue --------------------------------------------------------------------


class TestRevenueTool:
    def test_calls_service_once_with_resolved_window_and_currency(self, install):
        rec = install(RecordingFinanceService(results={"revenue": make_revenue_response()}))
        result = REVENUE_TOOL.run(SESSION, {"period": "yesterday"})
        method, session, kwargs = rec.calls[0]
        assert method == "revenue"
        assert len(rec.calls) == 1
        assert session is SESSION
        assert kwargs["currency"] == "INR"
        start, end = kwargs["start"], kwargs["end"]
        assert start < end
        assert start.tzinfo is not None
        assert result["tool"] == "revenue"

    def test_envelope_echoes_period_and_verbatim_data(self, install):
        canned = make_revenue_response()
        install(RecordingFinanceService(results={"revenue": canned}))
        result = REVENUE_TOOL.run(SESSION, RevenueToolInput())
        assert result["period"] == canned.period.model_dump(mode="json")
        expected = canned.model_dump(mode="json", exclude={"period"})
        assert result["data"] == expected

    def test_output_is_json_serializable(self, install):
        install(RecordingFinanceService(results={"revenue": make_revenue_response()}))
        payload = REVENUE_TOOL.run(SESSION, {"period": "this_week"})
        assert json.loads(json.dumps(payload)) == payload

    def test_no_recalculation_inconsistent_numbers_pass_through(self, install):
        # Deliberately impossible numbers: if the tool recomputed anything,
        # these would come back normalized instead of echoed verbatim.
        weird = RevenueMetricsResponse(
            period=make_period(),
            currency="USD",
            gross_revenue_minor=7,
            razorpay_fee_minor=3,
            razorpay_tax_minor=1,
            net_revenue=NetRevenueVariants(
                net_of_refunds_minor=999,  # != 7 - anything sensible
                net_of_fees_minor=-44,
                net_of_refunds_and_fees_minor=13,
            ),
            refunds=RefundMetricsBlock(
                refund_count=8,
                refund_amount_minor=5,
                refund_rate=MetricRate(value=41.0, basis="weird_basis"),
            ),
            other_currency_transactions_excluded=2,
        )
        install(RecordingFinanceService(results={"revenue": weird}))
        data = REVENUE_TOOL.run(SESSION, {"period": "today"})["data"]
        assert data["net_revenue"]["net_of_refunds_minor"] == 999
        assert data["net_revenue"]["net_of_fees_minor"] == -44
        assert data["refunds"]["refund_rate"]["value"] == 41.0
        assert data["refunds"]["refund_rate"]["basis"] == "weird_basis"


# --- payment performance -------------------------------------------------------


class TestPaymentPerformanceTool:
    def test_delegates_with_named_period_parameters(self, install):
        rec = install(
            RecordingFinanceService(
                results={"payment_performance": make_performance_response()}
            )
        )
        result = PAYMENT_PERFORMANCE_TOOL.run(
            SESSION, {"period": "previous_month", "currency": "usd"}
        )
        _, _, kwargs = rec.calls[0]
        assert kwargs["currency"] == "USD"  # normalized at the input boundary
        start, end = kwargs["start"], kwargs["end"]
        assert start < end and start.tzinfo is not None
        assert result["tool"] == "payment_performance"

    def test_data_matches_engine_response_exactly(self, install):
        canned = make_performance_response()
        install(
            RecordingFinanceService(results={"payment_performance": canned})
        )
        result = PAYMENT_PERFORMANCE_TOOL.run(
            SESSION, PaymentPerformanceToolInput()
        )
        assert result["period"] == canned.period.model_dump(mode="json")
        assert result["data"] == canned.model_dump(mode="json", exclude={"period"})


# --- refunds ----------------------------------------------------------------------


class TestRefundTool:
    def test_reuses_engine_revenue_call_not_a_second_aggregation(self, install):
        rec = install(RecordingFinanceService(results={"revenue": make_revenue_response()}))
        REFUND_TOOL.run(SESSION, {"period": "this_month"})
        assert [name for name, _, _ in rec.calls] == ["revenue"]

    def test_returns_only_refund_fields_plus_denominator(self, install):
        canned = make_revenue_response()
        install(RecordingFinanceService(results={"revenue": canned}))
        result = REFUND_TOOL.run(SESSION, RefundToolInput())
        assert result["data"] == {
            "refund_count": canned.refunds.refund_count,
            "refund_amount_minor": canned.refunds.refund_amount_minor,
            "refund_rate": canned.refunds.refund_rate.model_dump(mode="json"),
            "gross_revenue_minor": canned.gross_revenue_minor,
        }
        # No revenue-only internals leak into the refund view.
        assert "net_revenue" not in result["data"]
        assert "razorpay_fee_minor" not in result["data"]

    def test_period_echoed_for_interpretation(self, install):
        canned = make_revenue_response()
        install(RecordingFinanceService(results={"revenue": canned}))
        result = REFUND_TOOL.run(SESSION, {})
        assert result["period"]["start"] == canned.period.start.isoformat()


# --- settlements ---------------------------------------------------------------


class TestSettlementTool:
    def test_delegates_to_engine_summary_with_date_filters(self, install):
        rec = install(
            RecordingFinanceService(results={"summary": make_summary_response()})
        )
        SETTLEMENT_TOOL.run(
            SESSION,
            {
                "start_date": "2026-03-01",
                "end_date": "2026-03-11",
                "currency": "inr",
            },
        )
        _, session, kwargs = rec.calls[0]
        assert session is SESSION
        assert kwargs == {
            "start_date": date(2026, 3, 1),
            "end_date": date(2026, 3, 11),
            "currency": "INR",
        }

    def test_projects_only_settlement_fields_per_currency(self, install):
        install(
            RecordingFinanceService(
                results={"summary": make_summary_response(currency="INR")}
            )
        )
        result = SETTLEMENT_TOOL.run(SESSION, SettlementToolInput())
        (item,) = result["data"]["currencies"]
        assert item == {
            "currency": "INR",
            "settlement_amount_minor": 950_000,
            "settlement_fees_minor": 10_100,
            "settlement_tax_minor": 1_700,
        }
        assert "gross_amount_minor" not in item
        assert "net_of_refunds_minor" not in item

    def test_window_and_filter_echo(self, install):
        canned = make_summary_response(currency=None, start_date=None, end_date=None)
        install(RecordingFinanceService(results={"summary": canned}))
        result = SETTLEMENT_TOOL.run(SESSION, SettlementToolInput())
        assert result["window"] == {"start_date": None, "end_date": None, "timezone": "UTC"}
        assert result["filters"] == {"currency": None}


# --- trends ------------------------------------------------------------------------


class TestTrendTool:
    def test_forwards_metric_granularity_currency_and_ist_clock(self, install):
        rec = install(RecordingFinanceService(results={"trend": make_trend_response()}))
        TREND_TOOL.run(
            SESSION,
            {"metric": "gross_revenue", "granularity": "week", "currency": "inr"},
        )
        _, _, kwargs = rec.calls[0]
        assert kwargs["metric"] == "gross_revenue"
        assert kwargs["granularity"] == "week"
        assert kwargs["currency"] == "INR"
        assert kwargs["now"].tzinfo is REPORTING_TIMEZONE

    def test_trend_payload_verbatim(self, install):
        canned = make_trend_response()
        install(RecordingFinanceService(results={"trend": canned}))
        result = TREND_TOOL.run(SESSION, TrendToolInput(metric="gross_revenue"))
        assert result["tool"] == "trends"
        assert result["data"] == canned.model_dump(mode="json")


# --- financial summary ----------------------------------------------------------


class TestFinancialSummaryTool:
    def test_passes_all_supported_filters_to_engine(self, install):
        rec = install(
            RecordingFinanceService(results={"summary": make_summary_response()})
        )
        FINANCIAL_SUMMARY_TOOL.run(
            SESSION,
            {
                "start_date": "2026-03-01",
                "end_date": "2026-03-11",
                "status": "captured",
                "method": "upi",
            },
        )
        _, _, kwargs = rec.calls[0]
        assert kwargs["start_date"] == date(2026, 3, 1)
        assert kwargs["end_date"] == date(2026, 3, 11)
        assert kwargs["status"] == "captured"
        assert kwargs["method"] == "upi"
        assert kwargs["currency"] is None

    def test_full_multi_currency_passthrough(self, install):
        second = make_currency_summary().model_copy(update={"currency": "USD"})
        canned = make_summary_response(currencies=[make_currency_summary(), second])
        install(RecordingFinanceService(results={"summary": canned}))
        result = FINANCIAL_SUMMARY_TOOL.run(SESSION, FinancialSummaryToolInput())
        assert result["tool"] == "financial_summary"
        assert result["filters"] == {"currency": None, "status": None, "method": None}
        assert result["window"]["timezone"] == "UTC"
        assert [c["currency"] for c in result["data"]["currencies"]] == ["INR", "USD"]
        assert json.loads(json.dumps(result)) == result


# --- input validation -------------------------------------------------------------


class TestInputValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {"period": "last_year"},
            {"period": ""},
            {"metric": "profit_margin"},
            {"granularity": "quarter"},
            {"currency": "us"},
            {"currency": ""},
            {"unknown_field": 1},
        ],
    )
    def test_invalid_inputs_raise_typed_error(self, install, payload):
        install(RecordingFinanceService())
        tool = get_finance_tool("trends")
        if "metric" in payload or "granularity" in payload:
            base = {"metric": "gross_revenue"}
            base.update(payload)
            payload = base
        else:
            # Period/currency rules live on the single-currency tools;
            # blank currency is *valid* on the summary-style tools.
            tool = get_finance_tool("revenue")
            if "unknown_field" in payload:
                tool = get_finance_tool("financial_summary")
        with pytest.raises(InvalidToolInputError) as excinfo:
            tool.run(SESSION, payload)
        assert excinfo.value.code == "invalid_input"
        assert excinfo.value.tool == tool.name

    def test_input_models_reject_bad_values_directly(self):
        with pytest.raises(ValidationError):
            RevenueToolInput(period="tomorrow")
        with pytest.raises(ValidationError):
            TrendToolInput(metric="net_profit")
        with pytest.raises(ValueError):
            SettlementToolInput(start_date=date(2026, 3, 11), end_date=date(2026, 3, 1))

    def test_currency_normalized_and_whitespace_tolerated(self, install):
        rec = install(RecordingFinanceService(results={"revenue": make_revenue_response()}))
        REVENUE_TOOL.run(SESSION, {"period": "today", "currency": "  inr "})
        assert rec.calls[0][2]["currency"] == "INR"

    def test_blank_optional_currency_becomes_unfiltered(self, install):
        rec = install(
            RecordingFinanceService(results={"summary": make_summary_response()})
        )
        SETTLEMENT_TOOL.run(SESSION, {"currency": "   "})
        assert rec.calls[0][2]["currency"] is None


# --- invocation ergonomics -------------------------------------------------------


class TestInvocationContract:
    def test_none_session_is_rejected_before_any_engine_call(self, install):
        rec = install(RecordingFinanceService(results={"revenue": make_revenue_response()}))
        with pytest.raises(InvalidToolInputError, match="session"):
            REVENUE_TOOL.run(None, {"period": "today"})
        assert rec.calls == []

    def test_non_mapping_non_model_payload_rejected(self, install):
        install(RecordingFinanceService())
        with pytest.raises(InvalidToolInputError, match="Expected"):
            REVENUE_TOOL.run(SESSION, 42)

    def test_every_tool_accepts_model_instance_or_dict(self, install):
        results = {
            "revenue": make_revenue_response(),
            "payment_performance": make_performance_response(),
            "refunds": make_revenue_response(),
            "summary": make_summary_response(),
            "trend": make_trend_response(),
        }
        install(RecordingFinanceService(results=results))
        payloads = {
            REVENUE_TOOL: {"period": "today"},
            PAYMENT_PERFORMANCE_TOOL: PaymentPerformanceToolInput(),
            REFUND_TOOL: {},
            SETTLEMENT_TOOL: SettlementToolInput(),
            TREND_TOOL: {"metric": "gross_revenue"},
            FINANCIAL_SUMMARY_TOOL: {},
        }
        for tool, payload in payloads.items():
            envelope = tool.run(SESSION, payload)
            assert envelope["tool"] == tool.name


# --- empty / no-data behavior -----------------------------------------------------


class TestEmptyData:
    def test_zero_degraded_responses_are_valid_outputs(self, install):
        install(RecordingFinanceService(results=empty_results()))
        envelopes = [
            REVENUE_TOOL.run(SESSION, {}),
            PAYMENT_PERFORMANCE_TOOL.run(SESSION, {}),
            TREND_TOOL.run(SESSION, {"metric": "refund_count"}),
            FINANCIAL_SUMMARY_TOOL.run(SESSION, {}),
        ]
        for envelope in envelopes:
            assert json.loads(json.dumps(envelope)) == envelope
        revenue_data = envelopes[0]["data"]
        assert revenue_data["gross_revenue_minor"] == 0
        assert revenue_data["refunds"]["refund_rate"]["value"] is None
        assert envelopes[1]["data"]["success_rate"]["value"] is None
        assert envelopes[2]["data"]["change_type"] == "no_activity"
        assert envelopes[3]["data"]["currencies"] == []


# --- error handling & secrecy ---------------------------------------------------


class TestErrorHandling:
    def test_engine_failure_becomes_sanitized_typed_error(self, install):
        bomb = RuntimeError(
            "(psycopg2.errors.SyntaxError) line 1: SELECT password FROM "
            "postgresql://admin:hunter2@db.internal:5432/prod"
        )
        install(
            RecordingFinanceService(
                results={}, errors={"revenue": bomb}
            )
        )
        with pytest.raises(FinanceEngineError) as excinfo:
            REVENUE_TOOL.run(SESSION, {"period": "today"})
        message = str(excinfo.value)
        assert "hunter2" not in message
        assert "postgres" not in message
        assert "SELECT" not in message
        assert excinfo.value.code == "engine_error"
        assert excinfo.value.tool == "revenue"
        assert excinfo.value.__cause__ is bomb  # server-side log context only

    def test_database_not_configured_keeps_public_message(self, install):
        clean = DatabaseNotConfiguredError(
            "DATABASE_URL is not configured; database features are disabled"
        )
        install(RecordingFinanceService(errors={"summary": clean}))
        with pytest.raises(FinanceEngineError) as excinfo:
            FINANCIAL_SUMMARY_TOOL.run(SESSION, {})
        assert "DATABASE_URL is not configured" in str(excinfo.value)

    @pytest.mark.parametrize("tool_name", ["settlements", "trends"])
    def test_failures_never_leak_dsn_across_tools(self, install, tool_name):
        dirty = ConnectionError(
            "could not connect to server postgresql://user:secret@10.0.0.9/db"
        )
        install(
            RecordingFinanceService(
                errors={"summary": dirty, "trend": dirty}
            )
        )
        with pytest.raises(FinanceEngineError) as excinfo:
            get_finance_tool(tool_name).run(
                SESSION,
                {"metric": "gross_revenue"} if tool_name == "trends" else {},
            )
        assert "postgresql://" not in str(excinfo.value)
        assert "secret" not in str(excinfo.value)


class TestOutputSafety:
    def test_envelopes_contain_only_json_native_values(self, install):
        results = {
            "revenue": make_revenue_response(),
            "payment_performance": make_performance_response(),
            "refunds": make_revenue_response(),
            "summary": make_summary_response(),
            "trend": make_trend_response(),
        }
        install(RecordingFinanceService(results=results))
        for tool in ALL_FINANCE_TOOLS:
            payload = tool.run(SESSION, {"metric": "gross_revenue"}
                               if tool.name == "trends" else {})
            encoded = json.dumps(payload)
            decoded = json.loads(encoded)
            assert decoded == payload
            assert isinstance(decoded["data"], dict)

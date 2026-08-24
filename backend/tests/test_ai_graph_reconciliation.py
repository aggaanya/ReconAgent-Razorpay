"""LangGraph reconciliation flow tests (Track 04 integration).

Pins how the reconcile_transactions tool enters the existing graph:
- the deterministic keyword fallback routes reconciliation questions to
  exactly one tool (no dashboard noise attached);
- LLM-planned selections pass arguments through and their envelopes
  reach interpretation untouched;
- sessionless execution works when (and only when) no selected tool
  needs a database;
- a real end-to-end run over the synthetic batch completes with honest
  metrics and no fabricated signals.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import app.ai.tools as tools_registry
from app.ai.graph import FinanceIntelligenceAgent
from app.ai.graph.nodes import STATUS_COMPLETED, STATUS_FAILED
from app.ai.llm import LLMConnectionError
from app.ai.tools.base import InvalidToolInputError
from app.schemas.reconciliation import ReconciliationStatus
from app.services.reconciliation_synthetic import CASE_DISTRIBUTION

SESSION = object()


class StubLLM:
    """Scripted planner/explainer, mirroring tests/test_ai_graph.py."""

    def __init__(
        self,
        plan_payload: Any = None,
        plan_error: Exception | None = None,
        explanation: str = "Reconciliation completed as reported.",
        explain_error: Exception | None = None,
    ) -> None:
        self.plan_payload = plan_payload
        self.plan_error = plan_error
        self.explanation = explanation
        self.explain_error = explain_error
        self.planner_calls: list[dict[str, Any]] = []
        self.interpret_calls: list[dict[str, Any]] = []

    def complete_json(self, message: str, *, system_prompt: str | None = None):
        self.planner_calls.append(
            {"message": message, "system_prompt": system_prompt}
        )
        if self.plan_error is not None:
            raise self.plan_error
        return json.loads(json.dumps(self.plan_payload))

    def explain_signals(self, signals, *, question=None, system_prompt=None):
        self.interpret_calls.append(
            {
                "signals": json.loads(json.dumps(signals)),
                "question": question,
                "system_prompt": system_prompt,
            }
        )
        if self.explain_error is not None:
            raise self.explain_error
        return SimpleNamespace(content=self.explanation)


class RecordingTool:
    """Registry stand-in that replays a canned envelope."""

    def __init__(self, name, input_model, envelope=None, requires_session=True):
        self.name = name
        self.description = f"{name} stub"
        self.input_model = input_model
        self.requires_session = requires_session
        self.envelope = envelope or {"tool": name, "data": {"stub": name}}
        self.calls: list[tuple[Any, Any]] = []

    def run(self, session, payload):
        self.calls.append((session, payload))
        if not isinstance(payload, self.input_model):
            try:
                self.input_model.model_validate(dict(payload))
            except ValidationError as exc:
                raise InvalidToolInputError(
                    f"Invalid input for '{self.name}' "
                    f"({exc.errors()[0]['msg']})",
                    tool=self.name,
                ) from exc
        return json.loads(json.dumps(self.envelope))


RECONCILE_ENVELOPE = {
    "tool": "reconcile_transactions",
    "dataset": {"source": "synthetic", "seed": 42, "size": 50},
    "data": {
        "total_records": 50,
        "matched_count": 35,
        "exception_count": 15,
        "unresolved_count": 1,
        "match_rate": 70.0,
        "accuracy": None,
        "processing_time_ms": 1.0,
        "throughput_records_per_second": 50000.0,
        "exception_breakdown": {"AMOUNT_MISMATCH": 4},
    },
    "exceptions": [
        {
            "source_transaction_id": "pay_x",
            "matched_transaction_id": "set_x",
            "status": "AMOUNT_MISMATCH",
            "expected_amount_minor": 100,
            "actual_amount_minor": 90,
            "difference_minor": -10,
            "currency": "INR",
            "reason": "settled amount differs from captured amount",
            "exception_type": "AMOUNT_MISMATCH",
        }
    ],
    "results": [],
}


@pytest.fixture
def stub_tools(monkeypatch):
    """Registry stubs for all seven tools, reconcile included."""
    from app.ai.tools.inputs import (
        FinancialSummaryToolInput,
        PaymentPerformanceToolInput,
        RefundToolInput,
        RevenueToolInput,
        SettlementToolInput,
        TrendToolInput,
    )
    from app.ai.tools.reconciliation import ReconcileToolInput

    stubs = {
        "revenue": RecordingTool("revenue", RevenueToolInput),
        "payment_performance": RecordingTool(
            "payment_performance", PaymentPerformanceToolInput
        ),
        "refunds": RecordingTool("refunds", RefundToolInput),
        "settlements": RecordingTool("settlements", SettlementToolInput),
        "trends": RecordingTool("trends", TrendToolInput),
        "financial_summary": RecordingTool(
            "financial_summary", FinancialSummaryToolInput
        ),
        "reconcile_transactions": RecordingTool(
            "reconcile_transactions",
            ReconcileToolInput,
            envelope=RECONCILE_ENVELOPE,
            requires_session=False,
        ),
    }
    monkeypatch.setattr(tools_registry, "FINANCE_TOOLS", dict(stubs))
    monkeypatch.setattr(
        tools_registry, "ALL_FINANCE_TOOLS", tuple(stubs.values())
    )
    monkeypatch.setattr(
        "app.ai.graph.prompts.ALL_FINANCE_TOOLS", tuple(stubs.values())
    )
    return stubs


# --- routing -------------------------------------------------------------------------


class TestRouting:
    def test_keyword_fallback_routes_reconciliation_questions_exclusively(
        self, stub_tools
    ):
        llm = StubLLM(plan_error=LLMConnectionError("planner down"))
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "Please reconcile my settlements against payments"
        )
        assert result.selection_source == "keyword_fallback"
        assert result.selected_tools == ["reconcile_transactions"]
        assert len(stub_tools["reconcile_transactions"].calls) == 1
        # Dashboard tools stay out of the run entirely.
        assert set(result.tool_results) == {"reconcile_transactions"}
        assert result.status == STATUS_COMPLETED

    @pytest.mark.parametrize(
        "question",
        [
            "reconcile transactions",
            "can you reconcile this for me?",
            "RECONCILIATION report please",
        ],
    )
    def test_keyword_rule_is_case_insensitive_and_self_contained(
        self, stub_tools, question
    ):
        llm = StubLLM(plan_payload={})  # empty plan forces fallback
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, question
        )
        assert result.selected_tools == ["reconcile_transactions"]

    def test_planner_selected_reconcile_passes_arguments_through(
        self, stub_tools
    ):
        plan = {
            "tools": [
                {
                    "tool": "reconcile_transactions",
                    "arguments": {"seed": 7, "size": 60},
                }
            ]
        }
        llm = StubLLM(plan_payload=plan)
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "run reconciliation"
        )
        tool = stub_tools["reconcile_transactions"]
        assert tool.calls[0][0] is SESSION
        assert tool.calls[0][1] == {"seed": 7, "size": 60}
        assert result.tool_results["reconcile_transactions"] == (
            RECONCILE_ENVELOPE
        )

    def test_planner_catalog_lists_the_tool_for_the_llm(self, stub_tools):
        llm = StubLLM(plan_payload={
            "tools": [{"tool": "reconcile_transactions", "arguments": {}}]
        })
        agent = FinanceIntelligenceAgent(llm)  # type: ignore[arg-type]
        agent.run(SESSION, "reconcile")
        assert '"reconcile_transactions"' in (
            llm.planner_calls[0]["message"]
        )


# --- facts channel -------------------------------------------------------------------


class TestFactsChannel:
    def test_envelope_reaches_interpretation_verbatim(self, stub_tools):
        llm = StubLLM(plan_payload={
            "tools": [{"tool": "reconcile_transactions", "arguments": {}}]
        })
        FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "explain the reconciliation"
        )
        sent = llm.interpret_calls[0]["signals"]["data"]
        assert sent["reconcile_transactions"] == RECONCILE_ENVELOPE

    def test_no_financial_signals_are_fabricated_for_recon_envelopes(
        self, stub_tools
    ):
        llm = StubLLM(plan_payload={
            "tools": [{"tool": "reconcile_transactions", "arguments": {}}]
        })
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "reconcile"
        )
        assert result.financial_signals == []
        sent = llm.interpret_calls[0]["signals"]["detected_signals"]
        assert sent == []

    def test_invalid_llm_arguments_surface_as_tool_errors(self, stub_tools):
        plan = {
            "tools": [
                {
                    "tool": "reconcile_transactions",
                    "arguments": {"size": 3},
                }
            ]
        }
        llm = StubLLM(plan_payload=plan)
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "tiny reconcile"
        )
        assert "reconcile_transactions" in result.tool_errors
        assert isinstance(
            result.tool_errors.get("reconcile_transactions"), str
        )
        assert result.status != STATUS_COMPLETED


# --- session policy through the graph -------------------------------------------------


class TestSessionPolicy:
    def test_reconcile_only_plan_runs_without_a_database_session(self):
        """Real registry + real tool: no DB anywhere in this path."""
        llm = StubLLM(plan_payload={
            "tools": [{"tool": "reconcile_transactions", "arguments": {}}]
        })
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            None, "reconcile"
        )
        assert result.status == STATUS_COMPLETED
        data = result.tool_results["reconcile_transactions"]["data"]
        assert data["total_records"] == 100

    def test_db_backed_plan_still_halts_without_a_session(self):
        llm = StubLLM(plan_payload={"tools": [
            {"tool": "revenue", "arguments": {}}
        ]})
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            None, "revenue?"
        )
        assert result.status == STATUS_FAILED
        assert any("session" in e.lower() for e in result.errors)

    def test_mixed_plan_holds_when_a_selected_tool_needs_the_db(self):
        llm = StubLLM(plan_payload={"tools": [
            {"tool": "reconcile_transactions", "arguments": {}},
            {"tool": "revenue", "arguments": {}},
        ]})
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            None, "reconcile and show revenue"
        )
        assert result.status == STATUS_FAILED
        assert result.tool_results == {}


# --- real end-to-end run ---------------------------------------------------------------


class TestRealSyntheticRun:
    def test_keyword_routed_real_run_completes_with_honest_metrics(self):
        llm = StubLLM(plan_error=LLMConnectionError("no planner"))
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "please reconcile my payments"
        )
        assert result.selection_source == "keyword_fallback"
        assert result.selected_tools == ["reconcile_transactions"]
        envelope = result.tool_results["reconcile_transactions"]
        assert envelope["data"]["total_records"] == 100
        assert envelope["data"]["matched_count"] == (
            CASE_DISTRIBUTION[ReconciliationStatus.MATCHED]
        )
        assert envelope["data"]["accuracy"] is None
        assert len(envelope["exceptions"]) == (
            envelope["data"]["exception_count"]
        )
        assert result.interpretation == llm.explanation
        assert result.errors == []

    def test_explainer_failure_keeps_the_full_report(self):
        llm = StubLLM(
            plan_payload={"tools": [
                {"tool": "reconcile_transactions", "arguments": {}}
            ]},
            explain_error=LLMConnectionError("explainer down"),
        )
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "reconcile"
        )
        assert result.interpretation is None
        assert result.status != STATUS_COMPLETED
        assert (
            result.tool_results["reconcile_transactions"]["data"][
                "matched_count"
            ]
            == CASE_DISTRIBUTION[ReconciliationStatus.MATCHED]
        )

    def test_unregistered_hostile_plan_never_executes_reconciliation(
        self, monkeypatch
    ):
        # Registry intact; a hostile name must be dropped by sanitization.
        llm = StubLLM(plan_payload={"tools": [
            {"tool": "reconcile_all_production_data", "arguments": {}}
        ]})
        result = FinanceIntelligenceAgent(llm).run(  # type: ignore[arg-type]
            SESSION, "wipe everything"
        )
        assert result.selected_tools == ["financial_summary"]  # fallback

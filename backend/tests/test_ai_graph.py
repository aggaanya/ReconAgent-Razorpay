"""LangGraph finance orchestration tests (fully mocked tools + LLM).

No database, no Razorpay, no OpenAI. Covers graph construction, every
tool-question shape, planner safety (allowlist enforcement, fallback),
tool/LLM failure handling, grounding of answers in tool results,
state serialization, determinism, and secret boundaries.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import app.ai.tools as tools_registry
from app.ai.graph import (
    FinanceAgentResult,
    FinanceIntelligenceAgent,
    build_finance_graph,
)
from app.ai.graph.nodes import STATUS_COMPLETED, STATUS_FAILED, STATUS_PARTIAL
from app.ai.graph.prompts import (
    INTERPRETATION_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    tool_catalog,
)
from app.ai.llm import (
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
)
from app.ai.tools.base import FinanceEngineError, InvalidToolInputError
from app.ai.tools.inputs import (
    FinancialSummaryToolInput,
    PaymentPerformanceToolInput,
    RefundToolInput,
    RevenueToolInput,
    SettlementToolInput,
    TrendToolInput,
)

SESSION = object()  # sentinel passed through config; must never enter state


# --- stubs ---------------------------------------------------------------------


class StubLLM:
    """Duck-typed LLMService with scripted planner/explainer behavior."""

    def __init__(
        self,
        plan_payload: Any = None,
        plan_error: LLMError | None = None,
        explanation: str = "The data shows revenue increased.",
        explain_error: LLMError | None = None,
    ) -> None:
        self.plan_payload = plan_payload
        self.plan_error = plan_error
        self.explanation = explanation
        self.explain_error = explain_error
        self.planner_calls: list[dict[str, Any]] = []
        self.interpret_calls: list[dict[str, Any]] = []

    def complete_json(self, message: str, *, system_prompt: str | None = None):
        self.planner_calls.append({"message": message, "system_prompt": system_prompt})
        if self.plan_error is not None:
            raise self.plan_error
        return json.loads(json.dumps(self.plan_payload))  # deep copy

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
    """Stands in for a registered FinanceTool; replays results/errors.

    Like the real ``FinanceTool.run``, raw dict payloads are validated
    through the input model first, so invalid LLM-proposed arguments fail
    exactly as they would in production.
    """

    def __init__(self, name, input_model, envelope=None, error=None):
        self.name = name
        self.description = f"{name} stub"
        self.input_model = input_model
        self.envelope = envelope or {"tool": name, "data": {"stub": name}}
        self.error = error
        self.calls: list[tuple[Any, Any]] = []

    def run(self, session, payload):
        self.calls.append((session, payload))
        if isinstance(self.error, Exception):
            raise self.error
        if not isinstance(payload, self.input_model):
            try:
                self.input_model.model_validate(dict(payload))
            except ValidationError as exc:
                raise InvalidToolInputError(
                    f"Invalid input for '{self.name}' " f"({exc.errors()[0]['msg']})",
                    tool=self.name,
                ) from exc
        return json.loads(json.dumps(self.envelope))


@pytest.fixture
def stub_tools(monkeypatch):
    """Replace the registry with recording stubs for all six tools."""
    stubs = {
        "revenue": RecordingTool("revenue", RevenueToolInput),
        "payment_performance": RecordingTool(
            "payment_performance",
            PaymentPerformanceToolInput,
            envelope={
                "tool": "payment_performance",
                "period": {"start": "2026-03-11T00:00:00+05:30"},
                "data": {"transaction_volume": 120, "success_rate": {"value": 91.67}},
            },
        ),
        "refunds": RecordingTool("refunds", RefundToolInput),
        "settlements": RecordingTool("settlements", SettlementToolInput),
        "trends": RecordingTool("trends", TrendToolInput),
        "financial_summary": RecordingTool(
            "financial_summary",
            FinancialSummaryToolInput,
            envelope={
                "tool": "financial_summary",
                "window": {"timezone": "UTC"},
                "filters": {},
                "data": {"currencies": []},
            },
        ),
    }
    # One deliberately failing tool available on demand per test:
    monkeypatch.setattr(tools_registry, "FINANCE_TOOLS", dict(stubs))
    monkeypatch.setattr(tools_registry, "ALL_FINANCE_TOOLS", tuple(stubs.values()))
    monkeypatch.setattr("app.ai.graph.prompts.ALL_FINANCE_TOOLS", tuple(stubs.values()))
    return stubs


def make_agent(llm: StubLLM) -> FinanceIntelligenceAgent:
    return FinanceIntelligenceAgent(llm)  # type: ignore[arg-type]


REVENUE_PLAN = {"tools": [{"tool": "revenue", "arguments": {"period": "this_month"}}]}
MULTI_PLAN = {
    "tools": [
        {"tool": "revenue", "arguments": {}},
        {"tool": "trends", "arguments": {"metric": "gross_revenue"}},
        {"tool": "payment_performance", "arguments": {}},
    ]
}


# --- construction ----------------------------------------------------------------


class TestGraphConstruction:
    def test_compiled_graph_has_linear_node_set(self):
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        graph = build_finance_graph(llm)  # type: ignore[arg-type]
        nodes = set(graph.get_graph().nodes)
        assert {"plan", "execute_tools", "interpret"} <= nodes

    def test_agent_builds_from_settings(self, monkeypatch):
        from app.core.config import Settings

        monkeypatch.delenv("LLM_API_KEY", raising=False)
        settings = Settings(_env_file=None, llm_api_key="cfg-key-not-secret")
        agent = FinanceIntelligenceAgent.from_settings(settings)
        assert agent._llm_service.model == Settings(
            _env_file=None
        ).llm_model or agent._llm_service.model.startswith("gpt")

    def test_result_model_is_json_serializable(self):
        result = FinanceAgentResult(question="q?", status="completed")
        encoded = result.model_dump(mode="json")
        assert json.loads(json.dumps(encoded)) == {
            "question": "q?",
            "status": "completed",
            "selected_tools": [],
            "selection_source": "",
            "tool_results": {},
            "tool_errors": {},
            "financial_signals": [],
            "interpretation": None,
            "errors": [],
        }


# --- one-tool question flows -------------------------------------------------------


class TestSingleToolQuestions:
    @pytest.mark.parametrize(
        ("question", "plan", "tool_name"),
        [
            ("What was my revenue this month?", REVENUE_PLAN, "revenue"),
            (
                "How is payment performance today?",
                {"tools": [{"tool": "payment_performance", "arguments": {}}]},
                "payment_performance",
            ),
            (
                "Did we issue refunds yesterday?",
                {"tools": [{"tool": "refunds", "arguments": {}}]},
                "refunds",
            ),
            (
                "How much money was settled?",
                {"tools": [{"tool": "settlements", "arguments": {}}]},
                "settlements",
            ),
            (
                "Is gross revenue trending up?",
                {
                    "tools": [
                        {
                            "tool": "trends",
                            "arguments": {
                                "metric": "gross_revenue",
                                "granularity": "month",
                            },
                        }
                    ]
                },
                "trends",
            ),
            (
                "Give me a complete financial overview.",
                {"tools": [{"tool": "financial_summary", "arguments": {}}]},
                "financial_summary",
            ),
        ],
    )
    def test_question_routes_to_selected_tool(
        self, stub_tools, question, plan, tool_name
    ):
        llm = StubLLM(plan_payload=plan)
        result = make_agent(llm).run(SESSION, question)
        tool = stub_tools[tool_name]
        assert len(tool.calls) == 1
        assert tool.calls[0][0] is SESSION
        assert result.status == STATUS_COMPLETED
        assert result.selected_tools == [tool_name]
        assert result.tool_results[tool_name]["tool"] == tool_name
        assert result.interpretation == llm.explanation
        assert result.errors == []

    def test_planner_receives_catalog_and_interpretation_gets_signals(self, stub_tools):
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        make_agent(llm).run(SESSION, "What is my revenue this month?")
        planner_call = llm.planner_calls[0]
        assert planner_call["system_prompt"] == PLANNER_SYSTEM_PROMPT
        assert '"revenue"' in planner_call["message"]
        interpret_call = llm.interpret_calls[0]
        assert interpret_call["system_prompt"] == INTERPRETATION_SYSTEM_PROMPT
        assert interpret_call["question"] == "What is my revenue this month?"
        signals = interpret_call["signals"]
        assert signals["data"]["revenue"]["data"]["stub"] == "revenue"


# --- multi-tool flow -----------------------------------------------------------------


class TestMultiToolQuestion:
    def test_all_selected_tools_execute_and_results_stay_keyed_by_name(
        self, stub_tools
    ):
        llm = StubLLM(plan_payload=MULTI_PLAN)
        result = make_agent(llm).run(SESSION, "Why did revenue decrease?")
        assert list(result.tool_results) == [
            "revenue",
            "trends",
            "payment_performance",
        ]
        for name, tool in stub_tools.items():
            if name in result.tool_results:
                assert len(tool.calls) == 1
        assert result.status == STATUS_COMPLETED

    def test_tool_count_capped_at_limit(self, stub_tools):
        greedy_plan = {
            "tools": [{"tool": name, "arguments": {}} for name in stub_tools]
            + [{"tool": "revenue", "arguments": {}}]  # duplicate beyond cap
        }
        llm = StubLLM(plan_payload=greedy_plan)
        result = make_agent(llm).run(SESSION, "Everything about my finances.")
        assert len(result.selected_tools) == 4
        assert len(set(result.selected_tools)) == 4
        # Exactly the first four registry-order tools ran; the rest never did.
        expected_run = set(result.selected_tools)
        for name, tool in stub_tools.items():
            if name in expected_run:
                assert len(tool.calls) == 1
            else:
                assert len(tool.calls) == 0


# --- planner safety -------------------------------------------------------------------


class TestPlannerSafety:
    def test_unknown_llm_proposed_tool_is_dropped_not_executed(self, stub_tools):
        hostile_plan = {
            "tools": [
                {"tool": "drop_tables", "arguments": {}},
                {"tool": "__import__", "arguments": {}},
                {"tool": "revenue", "arguments": {}},
            ]
        }
        llm = StubLLM(plan_payload=hostile_plan)
        result = make_agent(llm).run(SESSION, "What is my revenue?")
        assert result.selected_tools == ["revenue"]
        assert stub_tools["revenue"].calls and all(
            len(t.calls) == 0 for n, t in stub_tools.items() if n != "revenue"
        )

    def test_non_dict_entries_and_arguments_filtered(self, stub_tools):
        messy_plan = {
            "tools": [
                42,
                {"tool": 7},
                {"tool": "revenue", "arguments": "not-a-dict"},
                "revenue",
                {"tool": "trends"},
            ]
        }
        llm = StubLLM(plan_payload=messy_plan)
        result = make_agent(llm).run(SESSION, "Revenue and trends please")
        assert result.selected_tools == ["revenue", "trends"]
        assert stub_tools["revenue"].calls[0][1] == {}

    def test_empty_or_malformed_plan_falls_back_to_keywords(self, stub_tools):
        llm = StubLLM(plan_payload={"tools": []})
        result = make_agent(llm).run(SESSION, "Did customers get refunds?")
        assert result.selection_source == "keyword_fallback"
        assert result.selected_tools == ["refunds"]

    def test_planner_llm_failure_uses_keyword_fallback(self, stub_tools):
        llm = StubLLM(plan_payload=REVENUE_PLAN, plan_error=LLMRateLimitError("429"))
        result = make_agent(llm).run(SESSION, "show me refund trends overview")
        assert result.selection_source == "keyword_fallback"
        assert result.selected_tools == ["refunds", "trends", "financial_summary"]
        # Fallback plans must be executable: the trend call carries a
        # valid default metric instead of an empty argument dict.
        trends_call = [c for n, t in stub_tools["trends"].calls for c in [t]]
        assert trends_call and trends_call[0] == {"metric": "gross_revenue"}
        assert result.status == STATUS_COMPLETED

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("how much was settled last week", ["settlements"]),
            ("did payments fail", ["payment_performance"]),
            ("give me an overall summary", ["financial_summary"]),
            ("what are my sales", ["revenue"]),
            ("explain my business", ["financial_summary"]),  # default
        ],
    )
    def test_keyword_rules_map_to_allowlist(self, stub_tools, question, expected):
        llm = StubLLM(plan_payload={}, plan_error=LLMConnectionError("down"))
        result = make_agent(llm).run(SESSION, question)
        assert result.selected_tools == expected
        assert result.status == STATUS_COMPLETED


# --- argument validation & tool failures ----------------------------------------------


class TestArgumentAndToolFailures:
    def test_invalid_llm_arguments_become_per_tool_errors(self, stub_tools):
        bad_args_plan = {
            "tools": [
                {"tool": "revenue", "arguments": {"period": "forever"}},
                {"tool": "financial_summary", "arguments": {}},
            ]
        }
        llm = StubLLM(plan_payload=bad_args_plan)
        result = make_agent(llm).run(SESSION, "Revenue overview please")
        assert "revenue" in result.tool_errors
        assert "financial_summary" in result.tool_results
        assert result.status == STATUS_PARTIAL

    def test_tool_engine_failure_is_contained(self, stub_tools):
        boom = FinanceEngineError("Finance engine failed while running 'settlements'.")
        stub_tools["settlements"].error = boom
        plan = {
            "tools": [
                {"tool": "settlements", "arguments": {}},
                {"tool": "revenue", "arguments": {}},
            ]
        }
        llm = StubLLM(plan_payload=plan)
        result = make_agent(llm).run(SESSION, "Settlements vs revenue?")
        assert "settlements" in result.tool_errors
        assert "postgresql" not in str(result.tool_errors)
        assert "revenue" in result.tool_results
        assert result.status == STATUS_PARTIAL
        # Failed tool's error still reached interpretation as an honest gap.
        assert "errors" in llm.interpret_calls[0]["signals"]

    def test_unexpected_tool_exception_is_sanitized(self, stub_tools, monkeypatch):
        def explode(session, payload):
            raise RuntimeError("SELECT * FROM secrets; password=hunter2")

        stub_tools["revenue"].run = explode
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        result = make_agent(llm).run(SESSION, "revenue?")
        assert "revenue" in result.tool_errors
        assert "hunter2" not in result.tool_errors["revenue"]
        assert "SELECT" not in result.tool_errors["revenue"]

    def test_missing_session_fails_graph_without_executing_tools(self, stub_tools):
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        result = FinanceIntelligenceAgent(llm)._graph.invoke(
            {
                "question": "revenue?",
                "plan": [],
                "selection_source": "",
                "tool_results": {},
                "tool_errors": {},
                "interpretation": None,
                "status": "failed",
                "errors": [],
            },
            config={},
        )
        assert any("session" in e.lower() for e in result["errors"])
        assert all(len(t.calls) == 0 for t in stub_tools.values())

    def test_no_session_via_agent_marks_failed(self, stub_tools):
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        result = make_agent(llm).run(None, "revenue?")
        assert result.status == STATUS_FAILED
        assert result.interpretation is None
        assert result.tool_results == {}


class TestStreamingTokens:
    def test_token_callback_receives_final_interpretation(self, stub_tools):
        tokens: list[str] = []
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        graph = build_finance_graph(llm, token_callback=tokens.append)  # type: ignore[arg-type]
        graph.invoke(
            {
                "question": "revenue?",
                "plan": [],
                "selection_source": "",
                "tool_results": {},
                "tool_errors": {},
                "interpretation": None,
                "status": "failed",
                "errors": [],
            },
            config={"configurable": {"finance_db_session": SESSION}},
        )
        assert tokens == [llm.explanation]

    def test_no_tokens_when_interpretation_fails(self, stub_tools):
        tokens: list[str] = []
        llm = StubLLM(plan_payload=REVENUE_PLAN, explain_error=LLMConnectionError("down"))
        graph = build_finance_graph(llm, token_callback=tokens.append)  # type: ignore[arg-type]
        graph.invoke(
            {
                "question": "revenue?",
                "plan": [],
                "selection_source": "",
                "tool_results": {},
                "tool_errors": {},
                "interpretation": None,
                "status": "failed",
                "errors": [],
            },
            config={"configurable": {"finance_db_session": SESSION}},
        )
        assert tokens == []

    def test_agent_stream_emits_token_then_result_then_done(self, stub_tools):
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        agent = FinanceIntelligenceAgent(llm)  # type: ignore[arg-type]
        events = list(
            agent.stream(lambda: iter([SESSION]), "revenue?")
        )
        kinds = [kind for kind, _ in events]
        assert kinds == ["token", "result"]  # internal "done" is consumed
        assert events[0] == ("token", llm.explanation)
        assert events[1][0] == "result"
        assert events[1][1].status == "completed"
        assert events[1][1].interpretation == llm.explanation


# --- LLM interpretation failure ------------------------------------------------------


class TestInterpretationFailure:
    def test_llm_failure_keeps_raw_data_and_sets_partial(self, stub_tools):
        llm = StubLLM(
            plan_payload=REVENUE_PLAN, explain_error=LLMConnectionError("timeout")
        )
        result = make_agent(llm).run(SESSION, "What is my revenue?")
        assert result.interpretation is None
        assert result.tool_results["revenue"]["tool"] == "revenue"
        assert result.status == STATUS_PARTIAL
        assert any("Interpretation" in e for e in result.errors)

    def test_everything_failing_yields_failed_status(self, stub_tools):
        llm = StubLLM(
            plan_payload=REVENUE_PLAN,
            explain_error=LLMError("dead"),
        )
        stub_tools["revenue"].error = InvalidToolInputError("bad", tool="revenue")
        result = make_agent(llm).run(SESSION, "revenue?")
        assert result.status == STATUS_PARTIAL  # errors present -> partial
        assert result.interpretation is None


# --- missing data -----------------------------------------------------------------------


class TestMissingData:
    def test_zero_degraded_envelopes_flow_through(self, stub_tools):
        stub_tools["revenue"].envelope = {
            "tool": "revenue",
            "period": {"start": "...", "end": "..."},
            "data": {"gross_revenue_minor": 0},
        }
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        result = make_agent(llm).run(SESSION, "revenue this week?")
        assert result.tool_results["revenue"]["data"]["gross_revenue_minor"] == 0
        assert result.status == STATUS_COMPLETED


# --- grounding & no fabrication -----------------------------------------------------------


class TestGrounding:
    def test_interpretation_receives_exactly_the_tool_envelopes(self, stub_tools):
        llm = StubLLM(plan_payload=MULTI_PLAN)
        make_agent(llm).run(SESSION, "Why did revenue decrease?")
        sent = llm.interpret_calls[0]["signals"]
        expected = {
            name: stub_tools[name].envelope
            for name in ("revenue", "trends", "payment_performance")
        }
        assert sent["data"] == expected

    def test_tool_results_are_never_mutated_by_interpretation(self, stub_tools):
        llm = StubLLM(
            plan_payload=REVENUE_PLAN,
            explanation="Sure - revenue was actually ₹9,000,000 (invented).",
        )
        result = make_agent(llm).run(SESSION, "revenue?")
        assert result.tool_results["revenue"] == stub_tools["revenue"].envelope
        assert "₹9,000,000" in result.interpretation  # words live only here

    def test_state_transitions_are_visible_in_final_state(self, stub_tools):
        llm = StubLLM(plan_payload=MULTI_PLAN)
        final = make_agent(llm)._graph.invoke(
            {
                "question": "why did revenue decrease?",
                "plan": [],
                "selection_source": "",
                "tool_results": {},
                "tool_errors": {},
                "interpretation": None,
                "status": "failed",
                "errors": [],
            },
            config={"configurable": {"finance_db_session": SESSION}},
        )
        assert final["selection_source"] == "llm"
        assert final["status"] == "completed"
        assert len(final["plan"]) == 3
        assert set(final["tool_results"]) == {
            "revenue",
            "trends",
            "payment_performance",
        }


# --- serialization / determinism / security ----------------------------------------------


class TestStateSafety:
    def test_final_state_is_json_serializable_and_session_free(self, stub_tools):
        llm = StubLLM(plan_payload=MULTI_PLAN)
        final = make_agent(llm)._graph.invoke(
            {
                "question": "overview",
                "plan": [],
                "selection_source": "",
                "tool_results": {},
                "tool_errors": {},
                "interpretation": None,
                "status": "failed",
                "errors": [],
            },
            config={"configurable": {"finance_db_session": SESSION}},
        )
        encoded = json.dumps(final, default=repr)
        assert SESSION.__repr__() not in encoded
        assert "finance_db_session" not in encoded

    def test_agent_result_round_trips_json(self, stub_tools):
        llm = StubLLM(plan_payload=MULTI_PLAN)
        result = make_agent(llm).run(SESSION, "why did revenue decrease?")
        decoded = FinanceAgentResult.model_validate_json(result.model_dump_json())
        assert decoded == result

    def test_identical_inputs_produce_identical_structures(self, stub_tools):
        llm = StubLLM(plan_payload=MULTI_PLAN)
        first = make_agent(llm).run(SESSION, "why did revenue decrease?")
        second = make_agent(llm).run(SESSION, "why did revenue decrease?")
        assert first == second

    def test_prompts_never_embed_secrets_or_env_values(self, stub_tools, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-super-secret-value")
        catalog = json.dumps(tool_catalog(), sort_keys=True)
        assert "sk-super-secret-value" not in catalog
        assert "api_key" not in PLANNER_SYSTEM_PROMPT.lower()

    def test_planner_prompt_contains_tool_routing_rules(self):
        assert "TOOL ROUTING RULES" in PLANNER_SYSTEM_PROMPT
        assert "revenue" in PLANNER_SYSTEM_PROMPT
        assert "financial_summary" in PLANNER_SYSTEM_PROMPT
        assert (
            "Do NOT use 'financial_summary' for revenue-specific questions"
            in PLANNER_SYSTEM_PROMPT
        )

    @pytest.mark.parametrize(
        "question",
        [
            "What was my revenue this month?",
            "Show me the biggest financial issues.",
            "How much have I paid in fees and taxes?",
            "Are there any refunds that look suspicious?",
        ],
    )
    def test_suggested_questions_route_to_valid_tools(self, stub_tools, question):
        llm = StubLLM(plan_payload={}, plan_error=LLMConnectionError("down"))
        result = make_agent(llm).run(SESSION, question)
        assert result.selection_source == "keyword_fallback"
        for tool_name in result.selected_tools:
            assert tool_name in stub_tools

    def test_graph_modules_have_no_forbidden_capabilities(self):
        import inspect

        import app.ai.graph.graph as graph_mod
        import app.ai.graph.nodes as nodes_mod
        import app.ai.graph.service as service_mod

        sources = "\n".join(
            inspect.getsource(m) for m in (graph_mod, nodes_mod, service_mod)
        )
        for forbidden in (
            "os.environ",
            "create_engine",
            "text(",
            "sessionmaker",
            "Repository",
            "requests.",
            "httpx.",
            "openai.",
        ):
            assert forbidden not in sources, forbidden

    def test_only_registered_tools_are_callable_through_registry(self, stub_tools):
        from app.ai.tools import get_finance_tool

        with pytest.raises(Exception):
            get_finance_tool("execute_arbitrary_python")


class TestConciseInterpretationPrompt:
    def test_interpretation_prompt_forbids_verbose_boilerplate(self):
        """The chat interpretation prompt must cap output complexity and ban
        verbose filler so dashboard answers stay concise."""
        lowered = INTERPRETATION_SYSTEM_PROMPT.lower()
        assert "2-4 short sentences" in lowered
        assert "never repeat the same metric" in lowered
        assert "never expose internal field names" in lowered
        assert "match_rate" in lowered  # only mentioned as a banned example
        assert "failure_reason" in lowered  # only mentioned as a banned example
        assert "plain labels" in lowered  # plain labels are mandated
        assert "match rate" in lowered
        assert "the system recorded" in lowered  # banned boilerplate
        assert "this represents the percentage" in lowered
        assert "narrative only" in lowered
        # Reconciliation narrations stop at the single most important exception:
        # no full severity-by-severity dump or mandatory minor-unit value.
        assert "severity-by-severity" in lowered
        assert "most important exception" in lowered
        assert "minor-unit value" in lowered
        # Categories must be translated to human-readable names and the answer
        # must hide implementation details and UI noise.
        assert "duplicate settlement" in lowered  # category translation
        assert "dataset seed" in lowered  # seed is an internal detail to hide
        assert "deterministic" in lowered  # word is banned from answers
        assert "backend" in lowered
        assert "ai narrative" in lowered  # UI label answers must not add
        assert "**refresh**" in lowered


# --- observability -------------------------------------------------------------------------


class TestObservability:
    def test_logs_record_lifecycle_without_secrets(self, stub_tools, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="app.ai.graph")
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        make_agent(llm).run(SESSION, "What is my revenue this month?")
        text = caplog.text
        assert "planned" in text
        assert "completed with status=completed" in text
        assert "sk-" not in text


# --- signal analysis integration (Phase 4A) -------------------------------------------------


def declining_trends_envelope() -> dict:
    """Realistic trends envelope: revenue down 20% period over period."""
    return {
        "tool": "trends",
        "data": {
            "metric": "gross_revenue",
            "granularity": "month",
            "timezone": "Asia/Kolkata",
            "current_period": {
                "start": "2026-03-01T00:00:00+05:30",
                "end": "2026-03-31T23:59:59+05:30",
                "value": 800_000,
            },
            "previous_period": {
                "start": "2026-02-01T00:00:00+05:30",
                "end": "2026-02-28T23:59:59+05:30",
                "value": 1_000_000,
            },
            "absolute_change": -200_000,
            "percentage_change": -20.0,
            "change_type": "normal",
        },
    }


class TestSignalAnalysisIntegration:
    def _declining_setup(self, stub_tools):
        stub_tools["trends"].envelope = declining_trends_envelope()
        stub_tools["payment_performance"].envelope = {
            "tool": "payment_performance",
            "period": {"start": "2026-03-11T00:00:00+05:30"},
            "data": {
                "transaction_volume": 120,
                "success_rate": {"value": 87.0},
                "failure_rate": {"value": 13.0},
            },
        }
        plan = {
            "tools": [
                {"tool": "trends", "arguments": {"metric": "gross_revenue"}},
                {"tool": "payment_performance", "arguments": {}},
            ]
        }
        return StubLLM(plan_payload=plan)

    def test_analyze_signals_node_executes_and_emits_typed_dicts(self):
        from app.ai.graph.nodes import analyze_signals_node

        update = analyze_signals_node(
            {
                "tool_results": {
                    "trends": declining_trends_envelope(),
                }
            },
            config={},
        )
        signals = update["financial_signals"]
        assert [s["signal_type"] for s in signals] == ["REVENUE_DECLINE"]
        assert signals[0]["severity"] == "MEDIUM"
        assert signals[0]["percentage_change"] == -20.0
        assert json.loads(json.dumps(signals)) == signals  # JSON-native

    def test_analyze_signals_node_failure_is_safe(self, monkeypatch):
        from app.ai.graph import nodes as nodes_mod

        class ExplodingAnalyzer:
            def __init__(self, *a, **k):
                pass

            def analyze(self, *_a, **_k):
                raise RuntimeError("boom")

        monkeypatch.setattr(nodes_mod, "SignalAnalyzer", ExplodingAnalyzer)
        update = nodes_mod.analyze_signals_node(
            {"tool_results": {"trends": declining_trends_envelope()}},
            config={},
        )
        assert update["financial_signals"] == []
        assert any("Signal analysis" in e for e in update["errors"])

    def test_tool_results_reach_the_signal_analyzer(self, stub_tools, monkeypatch):
        from app.ai.graph import nodes as nodes_mod

        seen: dict[str, Any] = {}

        class RecordingAnalyzer:
            def __init__(self, *a, **k):
                pass

            def analyze(self, tool_results):
                seen.update(tool_results)
                return []

        monkeypatch.setattr(nodes_mod, "SignalAnalyzer", RecordingAnalyzer)
        llm = StubLLM(plan_payload=MULTI_PLAN)
        make_agent(llm).run(SESSION, "why did revenue decrease?")
        assert set(seen) == {"revenue", "trends", "payment_performance"}
        assert seen["trends"]["data"]["stub"] == "trends"

    def test_financial_signals_stored_in_state_and_reach_llm(self, stub_tools):
        llm = self._declining_setup(stub_tools)
        agent = make_agent(llm)
        result = agent.run(SESSION, "Why did revenue decrease?")
        types = {s["signal_type"] for s in result.financial_signals}
        assert types == {
            "REVENUE_DECLINE",
            "PAYMENT_SUCCESS_LOW",
            "PAYMENT_FAILURE_HIGH",
            "REVENUE_PAYMENT_COINCIDENCE",
        }
        # The LLM received exactly the stored deterministic signals.
        sent = llm.interpret_calls[0]["signals"]["detected_signals"]
        assert sent == result.financial_signals

    def test_graph_state_holds_serializable_signals(self, stub_tools):
        llm = self._declining_setup(stub_tools)
        final = make_agent(llm)._graph.invoke(
            {
                "question": "why did revenue decrease?",
                "plan": [],
                "selection_source": "",
                "tool_results": {},
                "tool_errors": {},
                "financial_signals": [],
                "interpretation": None,
                "status": "failed",
                "errors": [],
            },
            config={"configurable": {"finance_db_session": SESSION}},
        )
        encoded = json.dumps(final["financial_signals"])
        decoded = json.loads(encoded)
        assert {s["signal_type"] for s in decoded} >= {"REVENUE_DECLINE"}

    def test_empty_signals_produce_no_fabricated_findings(self, stub_tools):
        # Flat, healthy envelopes: analyzer must stay silent and the LLM
        # must receive an empty detected_signals list alongside the facts.
        stub_tools["revenue"].envelope = {
            "tool": "revenue",
            "period": {},
            "data": {"currency": "INR", "gross_revenue_minor": 500_000},
        }
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        result = make_agent(llm).run(SESSION, "revenue?")
        assert result.financial_signals == []
        sent = llm.interpret_calls[0]["signals"]["detected_signals"]
        assert sent == []
        assert result.status == STATUS_COMPLETED
        # Facts channel is untouched by the empty findings.
        assert result.tool_results["revenue"]["data"]["gross_revenue_minor"] == 500_000

    def test_signal_failure_does_not_lose_tool_data(self, stub_tools, monkeypatch):
        from app.ai.graph import nodes as nodes_mod

        class ExplodingAnalyzer:
            def __init__(self, *a, **k):
                pass

            def analyze(self, *_a, **_k):
                raise RuntimeError("boom")

        monkeypatch.setattr(nodes_mod, "SignalAnalyzer", ExplodingAnalyzer)
        llm = self._declining_setup(stub_tools)
        result = make_agent(llm).run(SESSION, "Why did revenue decrease?")
        assert result.financial_signals == []
        assert set(result.tool_results) == {"trends", "payment_performance"}
        assert any("Signal analysis" in e for e in result.errors)
        assert result.status == STATUS_PARTIAL
        # Interpretation still happened over the raw facts.
        assert set(llm.interpret_calls[0]["signals"]["data"]) == {
            "trends",
            "payment_performance",
        }

    def test_graph_runs_analysis_before_interpretation(self, stub_tools, monkeypatch):
        from app.ai.graph import nodes as nodes_mod
        import app.ai.graph.graph as graph_mod
        from app.services.signal_analysis import SignalAnalyzer as RealAnalyzer

        order: list[str] = []

        class OrderRecordingAnalyzer(RealAnalyzer):
            def analyze(self, tool_results):
                order.append("analyze")
                return super().analyze(tool_results)

        monkeypatch.setattr(nodes_mod, "SignalAnalyzer", OrderRecordingAnalyzer)

        real_make_interpret = nodes_mod.make_interpret_node

        def recording_factory(llm_service, token_callback=None):
            node = real_make_interpret(llm_service, token_callback=token_callback)

            def inner(state, config):
                order.append("interpret")
                return node(state, config)

            return inner

        monkeypatch.setattr(graph_mod, "make_interpret_node", recording_factory)

        llm = self._declining_setup(stub_tools)
        make_agent(llm).run(SESSION, "Why did revenue decrease?")
        assert order == ["analyze", "interpret"]

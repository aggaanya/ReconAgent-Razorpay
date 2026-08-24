"""Graph nodes: plan -> execute tools -> interpret.

Responsibility split (docs/LANGGRAPH_ARCHITECTURE.md):

- ``plan_node`` — asks the LLMService which registered Finance Tools to
  call. The LLM's proposal is only a *suggestion*: every entry is
  re-validated against the live tool registry here, unknown names are
  dropped, arguments are left to the tools' Pydantic validation, and any
  LLM failure falls back to a deterministic keyword planner. The LLM can
  never invent a tool, a function, or SQL.
- ``execute_tools_node`` — runs the selected *existing* tools via
  ``app.ai.tools.get_finance_tool`` with the caller-supplied session from
  the RunnableConfig. Per-tool failures are captured as sanitized
  messages; one failing tool never aborts the rest.
- ``interpret_node`` — hands the collected structured signals (facts) to
  the existing LLMService for interpretation. If interpretation fails,
  the facts are still returned and the status says so.
"""

import json
import logging
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.ai.graph.prompts import (
    INTERPRETATION_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    planner_message,
)
from app.ai.graph.state import DB_SESSION_CONFIG_KEY, MAX_TOOLS_PER_QUESTION
from app.ai.llm import LLMError, LLMService
from app.ai.tools import FINANCE_TOOLS, FinanceToolError, get_finance_tool
from app.services.signal_analysis import SignalAnalyzer

logger = logging.getLogger(__name__)

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# Deterministic fallback used only when the LLM cannot be asked or gives
# nothing usable. Keys are substrings of the lower-cased question; each
# rule maps to a registered tool plus executable default arguments (the
# trend tool requires an explicit metric; gross revenue is the safest
# business-neutral default). Order matters: first match wins per keyword.
_DEFAULT_TREND_ARGS = {"metric": "gross_revenue"}
_KEYWORD_RULES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("reconcil", "reconcile_transactions", {}),
    ("refund", "refunds", {}),
    ("settl", "settlements", {}),
    ("trend", "trends", dict(_DEFAULT_TREND_ARGS)),
    ("growth", "trends", dict(_DEFAULT_TREND_ARGS)),
    ("compare", "trends", dict(_DEFAULT_TREND_ARGS)),
    ("success", "payment_performance", {}),
    ("fail", "payment_performance", {}),
    ("performance", "payment_performance", {}),
    ("transaction", "payment_performance", {}),
    ("overview", "financial_summary", {}),
    ("summary", "financial_summary", {}),
    ("overall", "financial_summary", {}),
    ("revenue", "revenue", {}),
    ("sales", "revenue", {}),
)


def _keyword_plan(question: str) -> list[dict[str, Any]]:
    """Deterministic allowlist-only selection from question keywords."""
    lowered = question.lower()
    if "reconcil" in lowered:
        # Reconciliation is a self-contained ask: its report already
        # covers matches and exceptions, so route it alone instead of
        # piling dashboard queries onto the same run.
        return [{"tool": "reconcile_transactions", "arguments": {}}]
    names: list[str] = []
    calls: list[dict[str, Any]] = []
    for needle, tool_name, arguments in _KEYWORD_RULES:
        if needle in lowered and tool_name not in names:
            names.append(tool_name)
            calls.append({"tool": tool_name, "arguments": dict(arguments)})
    return calls[:MAX_TOOLS_PER_QUESTION] or [
        {"tool": "financial_summary", "arguments": {}}
    ]


def _sanitize_llm_selection(payload: Any) -> list[dict[str, Any]]:
    """Filter an LLM plan down to registered tools with dict arguments.

    Anything unexpected (unknown names, non-dict arguments, wrong types)
    is dropped and logged — never trusted, never executed.
    """
    if not isinstance(payload, dict):
        return []
    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list):
        return []
    selections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_tools:
        if isinstance(entry, str):  # tolerate bare "tool" strings
            entry = {"tool": entry, "arguments": {}}
        if not isinstance(entry, dict):
            continue
        name = entry.get("tool")
        arguments = entry.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            continue
        if name not in FINANCE_TOOLS:
            logger.warning("Planner proposed unregistered tool %r; dropped", name)
            continue
        if name in seen:
            continue
        seen.add(name)
        selections.append({"tool": name, "arguments": arguments})
        if len(selections) >= MAX_TOOLS_PER_QUESTION:
            break
    return selections


def make_plan_node(llm_service: LLMService):
    """Planning node factory bound to one LLMService."""

    def plan_node(
        state: dict[str, Any], config: RunnableConfig
    ) -> dict[str, Any]:
        question = str(state.get("question", "")).strip()
        if not question:
            return {
                "plan": [],
                "selection_source": "none",
                "status": STATUS_FAILED,
                "errors": ["A non-empty question is required."],
            }

        selections: list[dict[str, Any]] = []
        source = "keyword_fallback"
        try:
            payload = llm_service.complete_json(
                planner_message(question), system_prompt=PLANNER_SYSTEM_PROMPT
            )
        except LLMError as exc:
            logger.warning("Planner LLM call failed (%s); using keyword fallback",
                           type(exc).__name__)
        else:
            selections = _sanitize_llm_selection(payload)
            if selections:
                source = "llm"
        if not selections:
            selections = _keyword_plan(question)

        logger.info(
            "Finance graph planned %d tool(s) via %s", len(selections), source
        )
        return {"plan": selections, "selection_source": source}

    return plan_node


def execute_tools_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """Run each planned tool once; collect envelopes and sanitized errors."""
    session = (config.get("configurable") or {}).get(DB_SESSION_CONFIG_KEY)
    results: dict[str, dict[str, Any]] = {}
    tool_errors: dict[str, str] = {}
    graph_errors: list[str] = []

    def _needs_session(call: dict[str, Any]) -> bool:
        tool = FINANCE_TOOLS.get(str(call.get("tool")))
        return bool(getattr(tool, "requires_session", True))

    if session is None and any(
        _needs_session(call) for call in state.get("plan", [])
    ):
        # Only halt when a selected tool actually needs the database;
        # sessionless tools (e.g. reconcile_transactions) still run.
        return {
            "tool_results": results,
            "tool_errors": tool_errors,
            "errors": ["No database session was provided to the finance graph."],
            "status": STATUS_FAILED,
            "halted": True,
        }

    for call in state.get("plan", []):
        name = call["tool"]
        arguments = call.get("arguments", {})
        started = time.perf_counter()
        try:
            envelope = get_finance_tool(name).run(session, arguments)
        except FinanceToolError as exc:
            # Tool errors are already sanitized at the tools boundary.
            tool_errors[name] = str(exc)
            logger.warning("Finance tool %r failed in %.0fms: %s",
                           name, (time.perf_counter() - started) * 1000, exc.code)
        except Exception as exc:  # absolute last resort — keep the graph alive
            logger.error("Unexpected failure in finance tool %r", name, exc_info=True)
            tool_errors[name] = f"Finance tool '{name}' failed unexpectedly."
        else:
            results[name] = envelope
            logger.info("Finance tool %r completed in %.0fms",
                        name, (time.perf_counter() - started) * 1000)

    update: dict[str, Any] = {
        "tool_results": results,
        "tool_errors": tool_errors,
    }
    if graph_errors:
        update["errors"] = graph_errors
    return update


def analyze_signals_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """Run the deterministic Signal Analysis Engine over tool results.

    Pure Python detection (no LLM, no database): facts in, typed signals
    out. A failure here must never lose the retrieved data — it degrades
    to an empty signal list plus a controlled error.
    """
    analyzer = SignalAnalyzer()
    try:
        detected = analyzer.analyze(state.get("tool_results", {}))
    except Exception:
        logger.error("Signal analysis failed", exc_info=True)
        return {
            "financial_signals": [],
            "errors": [
                "Signal analysis is unavailable; raw financial data is "
                "returned without derived findings."
            ],
        }
    logger.info("Signal analysis produced %d signal(s)", len(detected))
    return {
        "financial_signals": [signal.model_dump(mode="json") for signal in detected]
    }


def make_interpret_node(llm_service: LLMService):
    """Interpretation node factory bound to one LLMService."""

    def interpret_node(
        state: dict[str, Any], config: RunnableConfig
    ) -> dict[str, Any]:
        results = state.get("tool_results", {})
        tool_errors = state.get("tool_errors", {})
        detected_signals = state.get("financial_signals", [])
        prior_errors = list(state.get("errors", []))
        question = str(state.get("question", ""))

        signals: dict[str, Any] = {
            "data": results,
            "detected_signals": detected_signals,
        }
        if tool_errors:
            signals["errors"] = tool_errors

        interpretation: str | None = None
        new_errors = list(prior_errors)
        try:
            reply = llm_service.explain_signals(
                signals,
                question=question,
                system_prompt=INTERPRETATION_SYSTEM_PROMPT,
            )
            interpretation = reply.content
        except LLMError as exc:
            logger.warning("Interpretation LLM call failed (%s)", type(exc).__name__)
            new_errors.append("Interpretation is unavailable; the retrieved "
                              "financial data is returned unexplained.")

        if not results and not tool_errors:
            status = STATUS_FAILED
        elif new_errors or tool_errors or interpretation is None:
            status = STATUS_PARTIAL
        else:
            status = STATUS_COMPLETED

        logger.info("Finance graph completed with status=%s", status)
        return {
            "interpretation": interpretation,
            "status": status,
            "errors": new_errors,
        }

    return interpret_node

"""Graph nodes: plan -> execute tools -> analyze signals -> interpret.

Responsibility split (docs/LANGGRAPH_ARCHITECTURE.md):

- ``plan_node`` — asks the LLMService which registered Finance Tools to
  call. The LLM's proposal is only a *suggestion*: every entry is
  re-validated against the live tool registry here, unknown names are
  dropped, arguments are left to the tools' Pydantic validation, and any
  LLM failure falls back to a deterministic keyword planner. The LLM can
  never invent a tool, a function, or SQL.

- ``execute_tools_node`` — runs the selected existing tools via
  ``app.ai.tools.get_finance_tool`` with the caller-supplied session from
  the RunnableConfig. Per-tool failures are captured as sanitized
  messages; one failing tool never aborts the rest.

- ``analyze_signals_node`` — runs deterministic financial signal analysis
  over the retrieved facts. No LLM is used here.

- ``interpret_node`` — hands the collected structured signals and facts
  to the existing LLMService for natural-language interpretation.
  If interpretation fails, the facts are still returned and the graph
  degrades gracefully to a partial result.
"""

import logging
import re
import time
from datetime import date, datetime
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
from app.core.periods import REPORTING_TIMEZONE
from app.services.signal_analysis import SignalAnalyzer


logger = logging.getLogger(__name__)


STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


# ---------------------------------------------------------------------------
# Deterministic date normalizer
# ---------------------------------------------------------------------------

_PERIOD_TO_DATE: dict[str, str] = {}


def _resolve_period_name(name: str) -> str | None:
    """Resolve a period name to a YYYY-MM-DD string using IST clock."""
    now = datetime.now(tz=REPORTING_TIMEZONE)
    from app.core.periods import named_period

    try:
        start, _end = named_period(name, now)
    except ValueError:
        return None
    return start.date().isoformat()


def _normalize_date_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize date fields in tool arguments before Pydantic validation.

    The LLM sometimes generates period names ("today", "this_month") or
    malformed date strings ("August 2025", "2026-08") for date fields.
    This function resolves them to valid YYYY-MM-DD strings so Pydantic
    validation succeeds.
    """
    DATE_FIELDS = {"start_date", "end_date"}
    out = dict(arguments)

    for field in DATE_FIELDS:
        value = out.get(field)
        if value is None or isinstance(value, (date, datetime)):
            continue

        if not isinstance(value, str):
            continue

        s = value.strip()
        if not s:
            continue

        # Already valid ISO date (YYYY-MM-DD) — pass through.
        try:
            date.fromisoformat(s)
            continue
        except ValueError:
            pass

        # Period name ("today", "this_month", etc.)
        resolved = _resolve_period_name(s)
        if resolved is not None:
            out[field] = resolved
            continue

        # Partial ISO month ("2026-08") → first day of month.
        m = re.fullmatch(r"(\d{4})-(\d{2})", s)
        if m:
            out[field] = f"{m.group(1)}-{m.group(2)}-01"
            continue

        # Everything else: leave as-is and let Pydantic validation
        # produce the error message.

    return out


# ---------------------------------------------------------------------------
# Deterministic planner fallback
# ---------------------------------------------------------------------------

_DEFAULT_TREND_ARGS = {
    "metric": "gross_revenue",
}


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
    """Create a deterministic allowlist-only tool plan."""

    lowered = question.lower()

    # Reconciliation is a self-contained operation.
    if "reconcil" in lowered:
        return [
            {
                "tool": "reconcile_transactions",
                "arguments": {},
            }
        ]

    names: list[str] = []
    calls: list[dict[str, Any]] = []

    for needle, tool_name, arguments in _KEYWORD_RULES:
        if needle in lowered and tool_name not in names:
            names.append(tool_name)
            calls.append(
                {
                    "tool": tool_name,
                    "arguments": dict(arguments),
                }
            )

    return calls[:MAX_TOOLS_PER_QUESTION] or [
        {
            "tool": "financial_summary",
            "arguments": {},
        }
    ]


# ---------------------------------------------------------------------------
# Planner validation
# ---------------------------------------------------------------------------

def _sanitize_llm_selection(payload: Any) -> list[dict[str, Any]]:
    """Validate an LLM-generated tool plan against the registered tools.

    The LLM is never trusted to create tools or execute arbitrary functions.
    Only tools present in FINANCE_TOOLS are accepted.
    """

    if not isinstance(payload, dict):
        return []

    raw_tools = payload.get("tools")

    if not isinstance(raw_tools, list):
        return []

    selections: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in raw_tools:

        # Be tolerant of:
        # {"tool": "revenue"}
        if isinstance(entry, str):
            entry = {
                "tool": entry,
                "arguments": {},
            }

        if not isinstance(entry, dict):
            continue

        name = entry.get("tool")
        arguments = entry.get("arguments", {})

        if not isinstance(name, str):
            continue

        if not isinstance(arguments, dict):
            continue

        if name not in FINANCE_TOOLS:
            logger.warning(
                "Planner proposed unregistered tool %r; dropped",
                name,
            )
            continue

        if name in seen:
            continue

        seen.add(name)

        selections.append(
            {
                "tool": name,
                "arguments": arguments,
            }
        )

        if len(selections) >= MAX_TOOLS_PER_QUESTION:
            break

    return selections


# ---------------------------------------------------------------------------
# Plan node
# ---------------------------------------------------------------------------

_VALID_PERIODS = frozenset({
    "today", "yesterday", "this_week", "previous_week",
    "this_month", "previous_month",
})

# Tools whose input models accept a 'period' field.
_PERIOD_TOOLS = frozenset({"revenue", "payment_performance", "refunds"})


def _normalize_period_args(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize obviously broken arguments for period-based tools.

    Small LLMs sometimes emit non-string periods (None, int) or
    non-string currency values that fail Pydantic validation.  Only
    clearly broken types are fixed — invalid-but-plausible strings like
    "forever" are left to fail at tool validation as before.
    """
    for sel in selections:
        name = sel.get("tool", "")
        args = sel.get("arguments", {})
        if not isinstance(args, dict):
            continue

        if name in _PERIOD_TOOLS:
            period = args.get("period")
            if period is not None and not isinstance(period, str):
                args["period"] = "this_month"

        currency = args.get("currency")
        if currency is not None and not isinstance(currency, str):
            args.pop("currency", None)

    return selections


_PERIOD_KEYWORD_MAP: dict[tuple[str, ...], str] = {
    ("today",): "today",
    ("yesterday",): "yesterday",
    ("this week",): "this_week",
    ("last week", "previous week", "past week"): "previous_week",
    ("this month", "this_month"): "this_month",
    ("last month", "previous month", "past month"): "previous_month",
}


def _guess_period(question: str) -> str:
    """Heuristically map a question to a named period."""
    lowered = question.lower()
    for keywords, period in _PERIOD_KEYWORD_MAP.items():
        if any(kw in lowered for kw in keywords):
            return period
    return "this_month"


def _enforce_tool_routing(
    question: str, selections: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Post-LLM safety: ensure critical tool-question matches aren't missed.

    Small local LLMs sometimes ignore planner routing instructions and
    generate invalid arguments.  This deterministic check:
    1. Adds missing tools for unambiguous keyword matches.
    2. Sanitizes arguments for tools it adds.
    """
    lowered = question.lower()
    selected_names = {s["tool"] for s in selections}

    trend_keywords = (
        "trend", "trending", "compare", "comparison", "growth",
        "decrease", "increase", "drop", "rise", "change", "vs",
        "versus", "compared", "over time", "down", "up",
    )
    is_trend_question = any(kw in lowered for kw in trend_keywords)

    period = _guess_period(question)

    if (
        any(kw in lowered for kw in ("revenue", "income", "earnings"))
        and "revenue" not in selected_names
        and not is_trend_question
    ):
        selections = [
            s for s in selections if s["tool"] != "financial_summary"
        ]
        selections.insert(
            0, {"tool": "revenue", "arguments": {"period": period}}
        )

    if "refund" in lowered and "refunds" not in selected_names:
        selections.insert(
            0, {"tool": "refunds", "arguments": {"period": period}}
        )

    if "fee" in lowered or "tax" in lowered:
        if (
            "revenue" not in selected_names
            and "financial_summary" not in selected_names
            and not is_trend_question
        ):
            selections.insert(
                0,
                {"tool": "revenue", "arguments": {"period": period}},
            )

    if (
        ("settled" in lowered or "settlement" in lowered)
        and "reconcil" not in lowered
        and "settlements" not in selected_names
    ):
        selections.append({"tool": "settlements", "arguments": {}})

    if (
        any(
            kw in lowered
            for kw in (
                "success rate", "failure rate", "payment performance",
                "failed payment",
            )
        )
        and "payment_performance" not in selected_names
    ):
        selections.insert(
            0, {"tool": "payment_performance", "arguments": {"period": period}}
        )

    broad_keywords = (
        "issues", "problems", "concerns", "biggest", "overview",
        "health", "overall", "summary",
    )
    is_broad = any(kw in lowered for kw in broad_keywords)
    if (
        is_broad
        and not selections
        or (
            is_broad
            and all(
                s["tool"] not in ("financial_summary", "revenue", "refunds", "payment_performance")
                for s in selections
            )
        )
    ):
        selections.insert(
            0, {"tool": "financial_summary", "arguments": {}}
        )

    return selections[:MAX_TOOLS_PER_QUESTION]


def make_plan_node(llm_service: LLMService):
    """Create a planning node bound to one LLMService."""

    def plan_node(
        state: dict[str, Any],
        config: RunnableConfig,
    ) -> dict[str, Any]:

        question = str(
            state.get("question", "")
        ).strip()

        if not question:
            return {
                "plan": [],
                "selection_source": "none",
                "status": STATUS_FAILED,
                "errors": [
                    "A non-empty question is required."
                ],
            }

        selections: list[dict[str, Any]] = []
        source = "keyword_fallback"

        # ---------------------------------------------------------------
        # First try the configured LLM planner.
        # ---------------------------------------------------------------

        try:
            payload = llm_service.complete_json(
                planner_message(question),
                system_prompt=PLANNER_SYSTEM_PROMPT,
            )

            selections = _sanitize_llm_selection(payload)

            if selections:
                source = "llm"

        except LLMError as exc:
            logger.warning(
                "Planner LLM call failed (%s); using keyword fallback",
                type(exc).__name__,
            )

        except Exception:
            logger.exception(
                "Unexpected planner failure; using keyword fallback"
            )

        # ---------------------------------------------------------------
        # Deterministic fallback.
        # ---------------------------------------------------------------

        if not selections:
            selections = _keyword_plan(question)

        # ---------------------------------------------------------------
        # Post-LLM routing safety net.
        # ---------------------------------------------------------------

        selections = _enforce_tool_routing(question, selections)
        selections = _normalize_period_args(selections)

        logger.info(
            "Finance graph planned %d tool(s) via %s",
            len(selections),
            source,
        )

        return {
            "plan": selections,
            "selection_source": source,
        }

    return plan_node


# ---------------------------------------------------------------------------
# Tool execution node
# ---------------------------------------------------------------------------

def execute_tools_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Execute each planned finance tool once.

    Tool failures are isolated so one failing tool does not destroy the
    complete graph execution.
    """

    configurable = config.get("configurable") or {}

    session = configurable.get(
        DB_SESSION_CONFIG_KEY
    )

    results: dict[str, dict[str, Any]] = {}
    tool_errors: dict[str, str] = {}
    graph_errors: list[str] = []

    def _needs_session(call: dict[str, Any]) -> bool:
        tool = FINANCE_TOOLS.get(
            str(call.get("tool"))
        )

        return bool(
            getattr(tool, "requires_session", True)
        )

    planned_calls = state.get("plan", [])

    # ---------------------------------------------------------------
    # Validate database session requirements.
    # ---------------------------------------------------------------

    if session is None and any(
        _needs_session(call)
        for call in planned_calls
    ):
        return {
            "tool_results": results,
            "tool_errors": tool_errors,
            "errors": [
                "No database session was provided to the finance graph."
            ],
            "status": STATUS_FAILED,
            "halted": True,
        }

    # ---------------------------------------------------------------
    # Execute tools independently.
    # ---------------------------------------------------------------

    for call in planned_calls:

        name = call["tool"]
        arguments = _normalize_date_args(call.get("arguments", {}))

        started = time.perf_counter()

        try:
            envelope = get_finance_tool(
                name
            ).run(
                session,
                arguments,
            )

        except FinanceToolError as exc:

            tool_errors[name] = str(exc)

            logger.warning(
                "Finance tool %r failed in %.0fms: %s",
                name,
                (time.perf_counter() - started) * 1000,
                exc.code,
            )

        except Exception:

            logger.exception(
                "Unexpected failure in finance tool %r",
                name,
            )

            tool_errors[name] = (
                f"Finance tool '{name}' failed unexpectedly."
            )

        else:

            results[name] = envelope

            logger.info(
                "Finance tool %r completed in %.0fms",
                name,
                (time.perf_counter() - started) * 1000,
            )

    update: dict[str, Any] = {
        "tool_results": results,
        "tool_errors": tool_errors,
    }

    if graph_errors:
        update["errors"] = graph_errors

    return update


# ---------------------------------------------------------------------------
# Deterministic signal analysis node
# ---------------------------------------------------------------------------

def analyze_signals_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Run deterministic financial signal analysis.

    This node does NOT call the LLM.

    Input:
        Structured results from finance tools.

    Output:
        Typed/serialized financial signals.

    If signal analysis fails, raw financial data is preserved.
    """

    analyzer = SignalAnalyzer()

    try:
        detected = analyzer.analyze(
            state.get("tool_results", {})
        )

    except Exception:

        logger.exception(
            "Signal analysis failed"
        )

        return {
            "financial_signals": [],
            "errors": [
                "Signal analysis is unavailable; raw financial data "
                "is returned without derived findings."
            ],
        }

    logger.info(
        "Signal analysis produced %d signal(s)",
        len(detected),
    )

    return {
        "financial_signals": [
            signal.model_dump(mode="json")
            for signal in detected
        ]
    }


# ---------------------------------------------------------------------------
# Interpretation node
# ---------------------------------------------------------------------------

def make_interpret_node(llm_service: LLMService):
    """Create the final natural-language interpretation node.

    The LLM receives ONLY structured financial facts and detected signals.

    It must not:
    - invent financial numbers
    - invent transactions
    - invent exceptions
    - modify tool results
    - execute tools
    - generate SQL

    If the LLM is unavailable, the graph still returns the retrieved
    financial data and detected signals with a partial status.
    """

    def interpret_node(
        state: dict[str, Any],
        config: RunnableConfig,
    ) -> dict[str, Any]:

        # ---------------------------------------------------------------
        # Collect graph state.
        # ---------------------------------------------------------------

        results = state.get(
            "tool_results",
            {},
        )

        tool_errors = state.get(
            "tool_errors",
            {},
        )

        detected_signals = state.get(
            "financial_signals",
            [],
        )

        prior_errors = list(
            state.get(
                "errors",
                [],
            )
        )

        question = str(
            state.get(
                "question",
                "",
            )
        ).strip()

        # ---------------------------------------------------------------
        # Build a clean structured payload for the LLM.
        # ---------------------------------------------------------------

        signals: dict[str, Any] = {
            "data": results,
            "detected_signals": detected_signals,
        }

        if tool_errors:
            signals["errors"] = tool_errors

        interpretation: str | None = None

        new_errors = list(
            prior_errors
        )

        # ---------------------------------------------------------------
        # Call the LLM for interpretation.
        # ---------------------------------------------------------------

        if results or detected_signals or tool_errors:

            try:
                reply = llm_service.explain_signals(
                    signals,
                    question=question,
                    system_prompt=INTERPRETATION_SYSTEM_PROMPT,
                )

                # LLMCompletionResult.content is the correct field.
                content = getattr(
                    reply,
                    "content",
                    None,
                )

                if isinstance(content, str) and content.strip():

                    interpretation = content.strip()

                    logger.info(
                        "Financial interpretation generated successfully"
                    )

                else:

                    logger.warning(
                        "Interpretation LLM returned empty content"
                    )

                    new_errors.append(
                        "Interpretation returned no usable response."
                    )

            except LLMError as exc:

                # IMPORTANT:
                # Do not expose the provider's raw exception to the API.
                # The application should continue returning financial facts.
                logger.warning(
                    "Interpretation LLM call failed (%s)",
                    type(exc).__name__,
                )

                new_errors.append(
                    "Interpretation is temporarily unavailable; "
                    "the retrieved financial data and detected signals "
                    "are still available."
                )

            except Exception:

                # Last-resort protection. A provider/library error must
                # never crash the complete finance graph.
                logger.exception(
                    "Unexpected interpretation failure"
                )

                new_errors.append(
                    "AI interpretation failed unexpectedly; "
                    "the retrieved financial data and detected signals "
                    "are still available."
                )

        else:

            new_errors.append(
                "No financial data was available for interpretation."
            )

        # ---------------------------------------------------------------
        # Determine graph status.
        # ---------------------------------------------------------------

        if not results and not tool_errors and not detected_signals:

            status = STATUS_FAILED

        elif (
            interpretation is None
            or tool_errors
            # Upstream nodes can add sanitized errors (for example, signal
            # analysis failure) without preventing a narrative.  Those
            # errors still make the overall graph result partial.
            or prior_errors
            or len(new_errors) > len(prior_errors)
        ):

            status = STATUS_PARTIAL

        else:

            status = STATUS_COMPLETED

        logger.info(
            "Finance graph completed with status=%s",
            status,
        )

        # ---------------------------------------------------------------
        # Return the final graph state.
        # ---------------------------------------------------------------

        return {
            "interpretation": interpretation,
            "status": status,
            "errors": new_errors,
        }

    return interpret_node

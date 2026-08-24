"""Strongly typed LangGraph state for the finance intelligence flow.

Everything that travels between nodes is JSON-native (str / int / dict /
list / None) so the state stays serializable and inspectable:

- **never stored here:** database sessions, SQLAlchemy objects, API keys,
  credentials, environment values. The request-scoped DB session reaches
  nodes exclusively through the LangGraph ``RunnableConfig`` under
  :data:`DB_SESSION_CONFIG_KEY`, mirroring how FastAPI injects sessions
  per request.
- ``tool_results`` holds the *facts* (verbatim Finance Tool envelopes);
  ``interpretation`` holds the LLM's *words about those facts*. The two
  channels are never merged, preserving the FACTS vs INTERPRETATION
  boundary required by the financial safety rules.
"""

from typing import Any, TypedDict

#: ``RunnableConfig["configurable"]`` key under which nodes receive the
#: caller-supplied database session. The session is injected per run and
#: never copied into graph state.
DB_SESSION_CONFIG_KEY = "finance_db_session"

#: Hard cap on tools a single question may execute (read-only calls, but
#: bounded work is part of staying deterministic and auditable).
MAX_TOOLS_PER_QUESTION = 4


class ToolCall(TypedDict):
    """One validated tool selection produced by the planning node."""

    tool: str
    arguments: dict[str, Any]


class FinanceGraphState(TypedDict, total=False):
    """Channels flowing through the finance intelligence graph."""

    # input
    question: str
    # planning output
    plan: list[ToolCall]
    selection_source: str  # "llm" | "keyword_fallback"
    # tool execution output (facts — verbatim tool envelopes)
    tool_results: dict[str, dict[str, Any]]
    tool_errors: dict[str, str]
    # deterministic findings from the Signal Analysis Engine
    # (app.services.signal_analysis) over those envelopes; list of
    # FinancialSignal.model_dump(mode="json") dicts.
    financial_signals: list[dict[str, Any]]
    # LLM interpretation (words about the facts)
    interpretation: str | None
    # lifecycle / diagnostics
    status: str  # "completed" | "partial" | "failed"
    errors: list[str]
    # set by the executor on a fatal condition (e.g. missing session);
    # routes the graph straight to END instead of asking the LLM to
    # interpret data that was never retrieved.
    halted: bool


def initial_state(question: str) -> FinanceGraphState:
    """Fresh state for one question; defaults keep every channel defined."""
    return FinanceGraphState(
        question=question,
        plan=[],
        selection_source="",
        tool_results={},
        tool_errors={},
        financial_signals=[],
        interpretation=None,
        status="failed",
        errors=[],
        halted=False,
    )

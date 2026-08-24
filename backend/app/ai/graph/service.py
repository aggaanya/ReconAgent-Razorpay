"""Public entry point for the finance intelligence orchestration.

``FinanceIntelligenceAgent`` is the service the future chat API will call
(``agent.run(session, question) -> FinanceAgentResult``). It owns one
compiled LangGraph instance and one LLMService; the database session is
supplied per invocation and forwarded to nodes through the graph config —
never stored on the agent, the graph, or the state.

The result keeps FACTS (``tool_results`` — verbatim Finance Tool
envelopes) structurally separate from INTERPRETATION (LLM text), so no
consumer can mistake generated language for retrieved numbers.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ai.graph.graph import build_finance_graph
from app.ai.graph.state import DB_SESSION_CONFIG_KEY, initial_state
from app.ai.llm import LLMService

logger = logging.getLogger(__name__)


class FinanceAgentResult(BaseModel):
    """Structured outcome of one finance intelligence run."""

    question: str
    status: Literal["completed", "partial", "failed"]
    selected_tools: list[str] = Field(default_factory=list)
    selection_source: str = ""
    tool_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tool_errors: dict[str, str] = Field(default_factory=dict)
    #: Deterministic findings from the Signal Analysis Engine (facts about
    #: changes), structurally separate from the LLM's interpretation.
    financial_signals: list[dict[str, Any]] = Field(default_factory=list)
    interpretation: str | None = None
    errors: list[str] = Field(default_factory=list)


class FinanceIntelligenceAgent:
    """Read-only finance Q&A over the deterministic Finance Tools layer."""

    def __init__(self, llm_service: LLMService) -> None:
        self._llm_service = llm_service
        self._graph = build_finance_graph(llm_service)

    @property
    def llm_service(self) -> LLMService:
        """The shared LLM gateway (read-only access for API composition)."""
        return self._llm_service

    @classmethod
    def from_settings(cls, settings: Any) -> "FinanceIntelligenceAgent":
        return cls(LLMService.from_settings(settings))

    def close(self) -> None:
        """Release the underlying LLM client (lifespan shutdown / tests)."""
        self._llm_service.close()

    def run(self, session: Any, question: str) -> FinanceAgentResult:
        """Answer one finance question; read-only end to end."""
        final_state: dict[str, Any] = self._graph.invoke(
            initial_state(question),
            config={"configurable": {DB_SESSION_CONFIG_KEY: session}},
        )
        result = FinanceAgentResult(
            question=str(final_state.get("question", "")),
            status=final_state.get("status", "failed"),
            selected_tools=[call["tool"] for call in final_state.get("plan", [])],
            selection_source=final_state.get("selection_source", ""),
            tool_results=final_state.get("tool_results", {}),
            tool_errors=final_state.get("tool_errors", {}),
            financial_signals=final_state.get("financial_signals", []),
            interpretation=final_state.get("interpretation"),
            errors=final_state.get("errors", []),
        )
        logger.info(
            "Finance agent answered (status=%s, tools=%s)",
            result.status,
            ",".join(result.selected_tools) or "-",
        )
        return result

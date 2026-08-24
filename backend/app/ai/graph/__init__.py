"""LangGraph orchestration for the finance intelligence flow.

Layer position (docs/LANGGRAPH_ARCHITECTURE.md):

    Finance Tools -> LangGraph (this package) -> LLMService -> answer

- ``state.py``   — typed, JSON-native graph state.
- ``nodes.py``   — plan / execute_tools / interpret nodes.
- ``graph.py``   — linear graph assembly.
- ``service.py`` — ``FinanceIntelligenceAgent``, the public entry point.

The LLM only *selects* registered tools and *interprets* their structured
results. It can never execute SQL, touch repositories or sessions, or call
anything outside the fixed Finance Tool allowlist.
"""

from .service import FinanceAgentResult, FinanceIntelligenceAgent
from .graph import build_finance_graph
from .state import DB_SESSION_CONFIG_KEY, FinanceGraphState, ToolCall

__all__ = [
    "DB_SESSION_CONFIG_KEY",
    "FinanceAgentResult",
    "FinanceGraphState",
    "FinanceIntelligenceAgent",
    "ToolCall",
    "build_finance_graph",
]

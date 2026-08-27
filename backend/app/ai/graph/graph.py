"""Graph assembly: plan -> execute tools -> interpret (linear, read-only).

The flow is deliberately simple and deterministic — no branching, no
multi-agent loops. Every node is a factory-made callable bound to the
injected :class:`~app.ai.llm.LLMService`; the compiled graph is reusable
and stateless between runs.
"""

import logging
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from app.ai.graph.nodes import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    analyze_signals_node,
    execute_tools_node,
    make_interpret_node,
    make_plan_node,
)
from app.ai.graph.state import FinanceGraphState
from app.ai.llm import LLMService

logger = logging.getLogger(__name__)

PLAN_NODE = "plan"
EXECUTE_NODE = "execute_tools"
ANALYZE_NODE = "analyze_signals"
INTERPRET_NODE = "interpret"


def build_finance_graph(
    llm_service: LLMService,
    token_callback: Callable[[str], None] | None = None,
):
    """Compile the finance intelligence graph for one LLM service."""
    builder = StateGraph(FinanceGraphState)
    builder.add_node(PLAN_NODE, make_plan_node(llm_service))
    builder.add_node(EXECUTE_NODE, execute_tools_node)
    builder.add_node(ANALYZE_NODE, analyze_signals_node)
    interpret_node = (
        make_interpret_node(llm_service)
        if token_callback is None
        else make_interpret_node(llm_service, token_callback=token_callback)
    )
    builder.add_node(INTERPRET_NODE, interpret_node)

    builder.add_edge(START, PLAN_NODE)
    builder.add_edge(PLAN_NODE, EXECUTE_NODE)
    # A fatal execution condition (missing session) halts the graph before
    # analysis/interpretation: the LLM is never asked to explain data that
    # was not retrieved.
    builder.add_conditional_edges(
        EXECUTE_NODE,
        lambda state: END if state.get("halted") else ANALYZE_NODE,
        {ANALYZE_NODE: ANALYZE_NODE, END: END},
    )
    builder.add_edge(ANALYZE_NODE, INTERPRET_NODE)
    builder.add_edge(INTERPRET_NODE, END)

    logger.debug("Finance intelligence graph assembled")
    return builder.compile()


__all__ = [
    "ANALYZE_NODE",
    "EXECUTE_NODE",
    "INTERPRET_NODE",
    "PLAN_NODE",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "build_finance_graph",
]

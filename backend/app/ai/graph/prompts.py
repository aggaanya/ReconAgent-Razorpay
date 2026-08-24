"""Prompt construction for the finance intelligence graph.

Two prompts, both extending the boundary first encoded in
``app.ai.llm.DEFAULT_FINANCE_SYSTEM_PROMPT`` (LLM interprets pre-computed
signals; it never calculates, never invents):

- the **planner** prompt asks the LLM to pick tools from a catalog that is
  generated from the live Finance Tool registry — the LLM can only name
  tools that actually exist, and code re-validates every selection
  against the registry anyway;
- the **interpretation** prompt adds the financial-safety language rules:
  separate FACTS from POSSIBLE EXPLANATIONS, never introduce numbers that
  are not in the signals, and say plainly when the retrieved data cannot
  answer the question.

Prompts are built per call from registry metadata (no caching), so tests
that patch the tool registry automatically get consistent prompts.
"""

import json
import logging
from typing import Any

from app.ai.llm import DEFAULT_FINANCE_SYSTEM_PROMPT
from app.ai.tools import ALL_FINANCE_TOOLS

logger = logging.getLogger(__name__)


def tool_catalog() -> list[dict[str, Any]]:
    """Registry-derived catalog: name, description, argument JSON schema."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "arguments_schema": tool.input_model.model_json_schema(),
        }
        for tool in ALL_FINANCE_TOOLS
    ]


PLANNER_SYSTEM_PROMPT = (
    "You are the planner of a read-only finance intelligence system.\n"
    "You receive a user question about business finances and a catalog of "
    "available FINANCE TOOLS. Each tool queries deterministic, "
    "pre-computed financial metrics.\n"
    "Hard rules:\n"
    "1. Choose ONLY tools from the catalog, by their exact names.\n"
    "2. You may choose at most 4 tools; prefer the smallest set that can "
    "answer the question.\n"
    "3. For each tool provide an 'arguments' object matching its "
    "arguments_schema. Use only fields the schema defines.\n"
    "4. You do not compute or estimate any financial values yourself.\n"
    "5. Respond with ONE JSON object and nothing else:\n"
    '{"tools": [{"tool": "<catalog name>", "arguments": {...}}, ...]}\n'
    "If no tool fits, respond with {\"tools\": []}."
)


def planner_message(question: str) -> str:
    """User message for the planning call: question + tool catalog."""
    catalog = json.dumps(tool_catalog(), sort_keys=True)
    return (
        f"User question: {question}\n\n"
        f"Tool catalog (the only allowed tools):\n{catalog}"
    )


INTERPRETATION_SYSTEM_PROMPT = (
    DEFAULT_FINANCE_SYSTEM_PROMPT
    + "\n\nAdditional rules for your answer:\n"
    "- The signals include 'detected_signals' and 'data': findings produced by a "
    "deterministic analysis of the data (type, severity, direction, financial exposure, and "
    "evidence with exact numbers). Treat them as authoritative "
    "observations and explain them; never contradict or recompute them.\n"
    "- Separate FACTS (numbers present in the signals) from your "
    "INTERPRETATION and from POSSIBLE EXPLANATIONS.\n"
    '- Prefer wording such as "The data shows...", "This coincides '
    'with...", "A possible explanation is...", "The available data does '
    'not establish...".\n'
    "- For reconciliation outcomes and drift analysis (What Changed?), reference "
    "the backend-calculated financial exposure, exception priority breakdown, "
    "and major drivers verbatim. Do not attempt arithmetic or recalculate totals.\n"
    "- If some requested tools failed or returned errors, acknowledge "
    "which information could not be retrieved.\n"
    "- If the signals cannot answer the question, say so explicitly "
    "instead of filling gaps.\n"
    "- Never introduce monetary values, counts, percentages, causes, or "
    "customer behavior claims that are not supported by the signals. "
    "Causes may only be raised as hypotheses tied to detected signals, "
    "never stated as facts."
)

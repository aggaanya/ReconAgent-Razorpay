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
    """Registry-derived catalog: name, description, argument JSON schema.

    Compact format: only includes required fields, key optional fields,
    and fields with non-trivial defaults to minimize prompt tokens.
    """
    # Fields that are always included even if optional (they have meaningful
    # defaults or are commonly used).
    _KEY_OPTIONAL_FIELDS = frozenset({
        "period", "start_date", "end_date", "metric", "currency",
        "source", "seed", "size", "max_settlement_delay_days",
    })
    catalog = []
    for tool in ALL_FINANCE_TOOLS:
        schema = tool.input_model.model_json_schema()
        props = schema.get("properties", {})
        required = schema.get("required", [])
        compact_props = {}
        # Include required fields.
        for prop_name in required:
            prop_schema = props.get(prop_name, {})
            compact_props[prop_name] = {
                "type": prop_schema.get("type", "any"),
            }
            if "enum" in prop_schema:
                compact_props[prop_name]["enum"] = prop_schema["enum"]
            if "default" in prop_schema:
                compact_props[prop_name]["default"] = prop_schema["default"]
        # Include key optional fields if present in this tool's schema.
        for opt_name in _KEY_OPTIONAL_FIELDS:
            if opt_name in props and opt_name not in compact_props:
                prop_schema = props[opt_name]
                compact_props[opt_name] = {
                    "type": prop_schema.get("type", "any"),
                    "optional": True,
                }
                if "enum" in prop_schema:
                    compact_props[opt_name]["enum"] = prop_schema["enum"]
                if "default" in prop_schema:
                    compact_props[opt_name]["default"] = prop_schema["default"]
        catalog.append({
            "name": tool.name,
            "description": tool.description,
            "arguments": compact_props,
        })
    return catalog


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
    "If no tool fits, respond with {\"tools\": []}.\n"
    "\n"
    "TOOL ROUTING RULES:\n"
    "- Revenue questions (revenue, income, earnings, sales, gross): use 'revenue'.\n"
    "- Refund questions: use 'refunds'.\n"
    "- Settlement questions: use 'settlements'.\n"
    "- Payment success/failure/performance: use 'payment_performance'.\n"
    "- Trends, growth, comparisons, period-over-period: use 'trends'.\n"
    "- Broad overview, summary, or general health: use 'financial_summary'.\n"
    "- Reconciliation: use 'reconcile_transactions'.\n"
    "Do NOT use 'financial_summary' for revenue-specific questions — use 'revenue'.\n"
    "\n"
    "DATE HANDLING RULES:\n"
    "- For tools with a 'period' field (revenue, payment_performance, refunds), "
    "use one of the exact period names from the schema: "
    "'today', 'yesterday', 'this_week', 'previous_week', 'this_month', "
    "'previous_month'.\n"
    "- For tools with 'start_date' and 'end_date' fields (settlements, "
    "financial_summary), use ISO date format YYYY-MM-DD (e.g. '2026-08-01').\n"
    "- You may also use relative period names for start_date/end_date: "
    "'today', 'yesterday', 'this_week', 'previous_week', 'this_month', "
    "'previous_month' — these will be resolved automatically.\n"
    "- Do NOT use free-text dates like 'August 2025', 'last month', or "
    "'2026/08/25'. Use only ISO YYYY-MM-DD or exact period names."
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
    "never stated as facts.\n"
    "- Never recalculate, reinterpret, or convert any financial number. "
    "Use the exact values from the signals as-is. If a value is in "
    "minor units, present it in minor units and note the currency.\n"
    "- Do not mention any number that is not present in the supplied "
    "signals.\n"
    "\n"
    "RECONCILIATION-SPECIFIC RULES:\n"
    "- When explaining reconciliation results, distinguish clearly between "
    "match rate (matched_count / total_records * 100) and accuracy. "
    "Match rate is the percentage of records that the deterministic "
    "matching rules classified as MATCHED. It does NOT measure accuracy "
    "against ground truth. Never state or imply that the match rate "
    "equals accuracy.\n"
    "- The signals supply: total_records, matched_count, exception_count, "
    "match_rate, unresolved_count, exception_summary (total_financial_exposure_minor, "
    "critical_count, high_count, medium_count, low_count, and per-severity exposures), "
    "and sample_exceptions. Use these exact values verbatim. Do not recalculate "
    "any of them.\n"
    "- If the data includes an 'exception_summary', use it to describe "
    "total financial exposure, severity breakdown (critical/high/medium/low "
    "counts), and top exception categories.\n"
    "- Financial amounts appear in minor units (e.g. INR paise). When a "
    "currency field is present, you may express amounts in human-readable "
    "form by dividing minor units by 100 and prefixing with the currency "
    "symbol (e.g. 16065382 minor units with currency INR = Rs.1,60,653.82). "
    "Always cite the original minor-unit value from the signals first.\n"
    "- Mention unresolved records and their implications for the finance "
    "team.\n"
    "- Recommend specific human review actions for high-severity "
    "exceptions (critical and high priority).\n"
    "- Never claim that a match rate constitutes system accuracy without "
    "ground truth.\n"
    "- Present exception categories in descending order of count or "
    "financial impact, highlighting the most significant ones first."
)

RECONCILIATION_EXPLAIN_SYSTEM_PROMPT = (
    "You are an AI finance assistant explaining deterministic reconciliation results.\n"
    "Hard rules:\n"
    "1. All numbers in the signals are authoritative — never recalculate, reinterpret, "
    "convert, or invent figures. Use the exact values supplied.\n"
    "2. Do not mention any number that is not present in the supplied signals.\n"
    "3. The signals supply these exact values: total_records, matched_count, "
    "exception_count, match_rate, unresolved_count, exception_summary "
    "(total_financial_exposure_minor, critical_count, high_count, medium_count, "
    "low_count, critical_exposure_minor, high_exposure_minor, medium_exposure_minor, "
    "low_exposure_minor, top_exception_categories), and sample_exceptions. "
    "Use them verbatim.\n"
    "4. Distinguish match rate from accuracy: match rate is the % of records classified as "
    "MATCHED by deterministic rules; it does NOT measure accuracy vs ground truth.\n"
    "5. Financial amounts appear in minor units. You may express them in human-readable "
    "form (divide by 100, prefix with currency, e.g. INR 16065382 = Rs.1,60,653.82) "
    "but always cite the original minor-unit value first.\n"
    "6. Separate FACTS from INTERPRETATION and POSSIBLE EXPLANATIONS.\n"
    "7. Present exception categories by descending impact.\n"
    "8. Never claim match rate equals accuracy without ground truth.\n"
    "\n"
    "Output format — follow this structure exactly:\n"
    "1. Match rate — state the percentage and clarify it is NOT accuracy.\n"
    "2. Top exception categories — list the top categories with counts and financial impact.\n"
    "3. Severity — state critical/high/medium/low counts.\n"
    "4. Unresolved — state count and brief implication.\n"
    "5. Actions — 2-3 specific next steps for high-severity items.\n"
    "\n"
    "Be concise. Target 150-200 words. Do not repeat the input signals back. "
    "Do not include introductions or conclusions. Start directly with the match rate."
)

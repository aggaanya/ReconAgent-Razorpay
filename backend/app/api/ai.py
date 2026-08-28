"""AI Finance Controller API — natural-language finance Q&A and
deterministic batch reconciliation.

Conventions:

- Prefix ``/api/v1/ai``. ``POST /chat`` wraps the existing
  :class:`~app.ai.graph.service.FinanceIntelligenceAgent` (LangGraph)
  exactly as documented in docs/LANGGRAPH_ARCHITECTURE.md §7;
  ``POST /reconcile`` runs the deterministic Track 04 reconciliation
  tool directly and optionally attaches an LLM narrative. This layer
  adds validation, lifecycle, and HTTP error mapping only — no financial
  logic lives here.
- The agent is built once per process (pooled LLM client) and rebuilt
  automatically after a reset (tests/lifespan).
- A missing LLM key is a deployment state, not a crash: clean 503 with a
  message naming the missing variable (never any secret material). An
  unconfigured database answers 503 through the shared app-wide handler.
  ``/reconcile`` needs no database at all; it only requires the LLM when
  ``explain=true``.
- Both endpoints are strictly read-only: every number in a response comes
  from a deterministic engine envelope; ``answer`` is narrative about
  those facts and is never authoritative.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as OrmSession

from app.ai.graph.prompts import INTERPRETATION_SYSTEM_PROMPT, RECONCILIATION_EXPLAIN_SYSTEM_PROMPT
from app.ai.graph.service import FinanceAgentResult, FinanceIntelligenceAgent
from app.ai.llm import LLMError, LLMNotConfiguredError
from app.ai.tools import (
    RECONCILE_TRANSACTION_TOOL,
    FinanceToolError,
)
from app.core.cache import (
    compare_cache_key,
    evaluation_cache_key,
    reconciliation_cache,
    reconciliation_cache_key,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.ai import (
    AiChatRequest,
    AiChatResponse,
    AiReconcileRequest,
    AiReconcileResponse,
    AiReconcileCompareRequest,
    AiReconcileCompareResponse,
)
from app.schemas.reconciliation import (
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    ReconciliationEvaluationReport,
    ReconciliationResult,
)
from app.services.reconciliation import (
    build_report,
    evaluate_batch_with_ground_truth,
)
from app.services.reconciliation_drift import compare_runs
from app.services.reconciliation_synthetic import generate_synthetic_batch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

_agent: FinanceIntelligenceAgent | None = None

# Maximum length for LLM-generated text returned to the frontend.
_MAX_LLM_TEXT_LENGTH = 4_000


def _sanitize_llm_text(text: str | None) -> str | None:
    """Sanitize LLM-generated text before returning to the frontend.

    Removes any attempt at system-prompt injection, tool execution
    directives, or markdown-based data exfiltration. Truncates to a
    reasonable length.
    """
    if text is None:
        return None

    sanitized = text.strip()

    # Remove common prompt-injection patterns.
    import re
    injection_patterns = [
        r"(?i)ignore\s+(all\s+)?previous\s+instructions",
        r"(?i)ignore\s+(all\s+)?above\s+instructions",
        r"(?i)you\s+are\s+now\s+",
        r"(?i)disregard\s+(all\s+)?prior",
        r"(?i)new\s+instructions:",
        r"(?i)system\s*:\s*",
        r"(?i)<\|im_start\|>",
        r"(?i)<\|im_end\|>",
    ]
    for pattern in injection_patterns:
        sanitized = re.sub(pattern, "[redacted]", sanitized)

    # Truncate to bound the response size.
    if len(sanitized) > _MAX_LLM_TEXT_LENGTH:
        sanitized = sanitized[:_MAX_LLM_TEXT_LENGTH] + "..."

    return sanitized


# ---------------------------------------------------------------------------
# Deterministic drift narration (no LLM)
# ---------------------------------------------------------------------------


def _format_count_statement(previous: int, current: int) -> str:
    """Format a deterministic count-change statement."""
    if current > previous:
        return f"count increased from {previous} to {current}"
    if current < previous:
        return f"count decreased from {previous} to {current}"
    return f"count remained unchanged at {current}"


def _format_exposure_statement(change_minor: int) -> str:
    """Format a deterministic exposure-change statement in INR."""
    if change_minor > 0:
        return f"exposure increased by \u20b9{change_minor / 100:,.2f}"
    if change_minor < 0:
        return f"exposure decreased by \u20b9{abs(change_minor) / 100:,.2f}"
    return "exposure was unchanged"


def _drift_narration_signals(
    drift: Any,
    previous_exception_summary: Any,
    current_exception_summary: Any,
) -> dict[str, Any]:
    """Build deterministic structured facts for drift narration.

    Returns a dict with:
    - ``categories``: list of per-category narration items
    - ``severity_levels``: list of per-severity narration items
    - ``current_total_exposure_display``: human-readable total exposure
    - ``unresolved_exposure_display``: human-readable unresolved exposure
    - ``major_drivers``: list of pre-formatted driver strings
    """
    categories: list[dict[str, str]] = []
    for cd in drift.category_drifts:
        categories.append({
            "category": cd.category,
            "count_statement": _format_count_statement(
                cd.previous_count, cd.current_count
            ),
            "exposure_statement": _format_exposure_statement(
                cd.exposure_change_minor
            ),
        })

    severity_levels: list[dict[str, str]] = []
    for pd in drift.priority_drifts:
        severity_levels.append({
            "severity": pd.severity,
            "count_statement": _format_count_statement(
                pd.previous_count, pd.current_count
            ),
            "exposure_statement": _format_exposure_statement(
                pd.exposure_change_minor
            ),
        })

    curr_exp = drift.current_financial_exposure_minor
    curr_exp_display = f"\u20b9{curr_exp / 100:,.2f}"

    unresolved_exp = 0
    if current_exception_summary:
        unresolved_exp = getattr(
            current_exception_summary, "largest_unresolved_exposure_minor", 0
        ) or 0
    unresolved_display = f"\u20b9{unresolved_exp / 100:,.2f}" if unresolved_exp else "\u20b90.00"

    return {
        "categories": categories,
        "severity_levels": severity_levels,
        "current_total_exposure_display": curr_exp_display,
        "unresolved_exposure_display": unresolved_display,
        "major_drivers": list(drift.major_drivers),
    }


def _render_drift_narrative(facts: dict[str, Any]) -> str:
    """Render deterministic drift facts into a human-readable narrative.

    The output is entirely derived from pre-computed values; no arithmetic
    or financial calculation is performed here.
    """
    parts: list[str] = []

    for item in facts.get("categories", []):
        parts.append(
            f"{item['category']}: {item['count_statement']}; "
            f"{item['exposure_statement']}."
        )

    if facts.get("severity_levels"):
        parts.append("Severity breakdown:")
        for sev in facts["severity_levels"]:
            parts.append(
                f"  {sev['severity']}: {sev['count_statement']}; "
                f"{sev['exposure_statement']}."
            )

    total_exp = facts.get("current_total_exposure_display", "\u20b90.00")
    parts.append(f"Total financial exposure: {total_exp}.")

    unresolved = facts.get("unresolved_exposure_display")
    if unresolved and unresolved != "\u20b90.00":
        parts.append(f"Unresolved exposure: {unresolved}.")

    return "\n".join(parts)


def get_ai_agent() -> FinanceIntelligenceAgent:
    """FastAPI dependency returning the process-wide agent instance."""
    global _agent
    if _agent is None:
        try:
            _agent = FinanceIntelligenceAgent.from_settings(get_settings())
        except LLMNotConfiguredError as exc:
            # Unconfigured LLM integration: clear 503, no secrets echoed.
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _agent


def reset_ai_agent() -> None:
    """Close and discard the cached agent (lifespan shutdown / tests)."""
    global _agent
    if _agent is not None:
        _agent.close()
        _agent = None


def _to_response(result: FinanceAgentResult) -> AiChatResponse:
    """Project the graph result onto the public chat schema."""

    return AiChatResponse(
        question=result.question,
        status=result.status,
        answer=_sanitize_llm_text(result.interpretation),
        selected_tools=result.selected_tools,
        selection_source=result.selection_source,
        tool_results=result.tool_results,
        tool_errors=result.tool_errors,
        financial_signals=result.financial_signals,
        errors=result.errors,
    )


@router.post(
    "/chat",
    response_model=AiChatResponse,
    summary="Ask a finance question in natural language",
    description=(
        "Plans read-only Finance Tool calls for the question, executes them "
        "against synced records, derives deterministic financial signals, "
        "and returns an LLM-written explanation grounded in those signals. "
        "`tool_results` / `financial_signals` carry the exact numbers; "
        "`answer` is interpretation only."
    ),
)
def ai_chat(
    payload: AiChatRequest,
    agent: FinanceIntelligenceAgent = Depends(get_ai_agent),
    session: OrmSession = Depends(get_db),
) -> AiChatResponse:
    logger.info("AI chat question received (%d chars)", len(payload.question))
    result = agent.run(session, payload.question)
    return _to_response(result)


@router.post(
    "/chat/stream",
    summary="Stream AI finance chat response as Server-Sent Events",
    description=(
        "SSE variant of /chat. Streams token events as they arrive from "
        "the LLM, followed by a complete event with the full response."
    ),
)
def ai_chat_stream(
    payload: AiChatRequest,
    agent: FinanceIntelligenceAgent = Depends(get_ai_agent),
    session: OrmSession = Depends(get_db),
) -> StreamingResponse:
    logger.info("AI chat stream requested (%d chars)", len(payload.question))

    def event_generator():
        collected_tokens: list[str] = []
        result = None
        error_event = None

        try:
            for kind, data in agent.stream(
                lambda: iter([session]), payload.question
            ):
                if kind == "keepalive":
                    yield ": keepalive\n\n"
                elif kind == "token":
                    collected_tokens.append(str(data))
                    payload_data = json.dumps({"content": str(data)})
                    yield f"event: token\ndata: {payload_data}\n\n"
                elif kind == "result":
                    result = data
                elif kind == "error":
                    error_event = data
        except Exception:
            logger.exception("Stream failed unexpectedly")
            error_event = RuntimeError("AI response generation is unavailable.")

        if error_event is not None:
            error_payload = json.dumps({"message": "AI response generation is unavailable."})
            yield f"event: error\ndata: {error_payload}\n\n"
            return

        if result is not None:
            response = _to_response(result)
            complete_payload = json.dumps({"response": response.model_dump()})
            yield f"event: complete\ndata: {complete_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _reconcile_signals(
    summary: dict, exceptions: list[dict], seed: int, size: int, exception_summary: dict | None = None
) -> dict:
    """Structured facts handed to the LLM (never ground truth).

    The payload is intentionally compact: only the fields the LLM needs
    to narrate the reconciliation outcome.  Raw exception records are NOT
    included — only aggregate counts, exposure, and a short sample.

    All financial values are authoritative minor-unit integers supplied by
    the deterministic engine.  This function NEVER converts, formats, or
    recalculates any number — that responsibility belongs to the LLM
    narrative layer which receives the raw values plus the currency code.
    """
    signals: dict[str, Any] = {
        "data": {
            "total_records": summary.get("total_records"),
            "matched_count": summary.get("matched_count"),
            "exception_count": summary.get("exception_count"),
            "unresolved_count": summary.get("unresolved_count"),
            "match_rate": summary.get("match_rate"),
            "processing_time_ms": summary.get("processing_time_ms"),
            "throughput_records_per_second": summary.get("throughput_records_per_second"),
            "exception_breakdown": summary.get("exception_breakdown", {}),
        },
        "dataset": {
            "source": "synthetic",
            "seed": seed,
            "size": size,
        },
    }

    if exception_summary:
        signals["exception_summary"] = exception_summary
    else:
        signals["exception_summary"] = None

    if exceptions:
        sample: list[dict[str, Any]] = []
        for exc in exceptions[:5]:
            entry: dict[str, Any] = {
                "status": exc.get("status") or exc.get("exception_type"),
                "severity": exc.get("severity"),
                "reason": exc.get("reason", ""),
            }
            impact = exc.get("financial_impact_minor")
            if impact is not None:
                entry["financial_impact_minor"] = impact
            if exc.get("recommended_action"):
                entry["recommended_action"] = exc["recommended_action"]
            sample.append(entry)
        signals["sample_exceptions"] = sample
    else:
        signals["sample_exceptions"] = []

    return signals


@router.post(
    "/reconcile",
    response_model=AiReconcileResponse,
    summary="Run deterministic batch reconciliation over a synthetic dataset",
    description=(
        "Executes the reconcile_transactions Finance Tool — fixed matching "
        "rules over a seeded 50+ case synthetic batch — and returns the "
        "summary metrics, match rate, throughput, and the full exception "
        "list. With `explain=true` an LLM narrative is attached; it can "
        "only describe the returned numbers. `accuracy` is null at "
        "runtime by design: measured accuracy lives in the evaluation "
        "harness (scripts/reconcile_benchmark.py), never in serving."
    ),
)
def ai_reconcile(
    payload: AiReconcileRequest,
) -> AiReconcileResponse:
    agent: FinanceIntelligenceAgent | None = None
    if payload.explain:
        # Resolved lazily so explain=false works without any LLM key.
        agent = get_ai_agent()

    logger.info(
        "AI reconciliation requested (source=%s, seed=%d, size=%d)",
        payload.source,
        payload.seed,
        payload.size,
    )

    # ---------------------------------------------------------------
    # Check cache for previously computed deterministic results.
    # ---------------------------------------------------------------
    cache_key = reconciliation_cache_key(
        source=payload.source,
        seed=payload.seed,
        size=payload.size,
        max_settlement_delay_days=payload.max_settlement_delay_days,
        explain=payload.explain,
        question=payload.question,
    )
    cached = reconciliation_cache.get(cache_key)
    if cached is not None:
        logger.info("Reconciliation cache hit (key=%s)", cache_key)
        return cached

    try:
        envelope = RECONCILE_TRANSACTION_TOOL.run(
            None,
            {
                "source": payload.source,
                "seed": payload.seed,
                "size": payload.size,
                "max_settlement_delay_days": (
                    payload.max_settlement_delay_days
                ),
            },
        )
    except FinanceToolError as exc:
        # Sanitized at the tools boundary; surfaced without decoration.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    data = envelope["data"]
    exceptions = envelope["exceptions"]
    errors: list[str] = []
    answer: str | None = None
    if agent is not None:
        try:
            reply = agent.llm_service.explain_signals(
                _reconcile_signals(
                    data,
                    exceptions,
                    payload.seed,
                    payload.size,
                    envelope.get("exception_summary"),
                ),
                question=(
                    payload.question
                    or "Summarize this reconciliation outcome in at most 2-3 "
                    "short sentences for a business user: the match rate in "
                    "plain terms (for example '58% of records were matched'), "
                    "the exception with the largest financial impact, and "
                    "unresolved records only if worth flagging. Mention "
                    "important numbers only; do not repeat the same fact twice "
                    "and do not describe system-level details."
                ),
                system_prompt=RECONCILIATION_EXPLAIN_SYSTEM_PROMPT,
            )
            answer = reply.content
        except LLMError as exc:
            # The provider exception can include transport or provider
            # details.  Keep it in logs for diagnosis, but never return it
            # to an API consumer; the deterministic report remains usable.
            logger.warning(
                "Reconciliation explanation failed (%s)",
                type(exc).__name__,
            )
            errors.append(
                "AI narrative explanation is temporarily unavailable; the deterministic "
                "reconciliation data is still available."
            )

    status = "completed" if answer is not None else "partial"
    if not payload.explain:
        status = "completed"
    response = AiReconcileResponse(
        status=status,
        answer=_sanitize_llm_text(answer),
        total_records=data["total_records"],
        matched_count=data["matched_count"],
        exception_count=data["exception_count"],
        unresolved_count=data["unresolved_count"],
        match_rate=data["match_rate"],
        accuracy=None,  # ground truth never enters the serving path
        processing_time_ms=data["processing_time_ms"],
        throughput_records_per_second=data["throughput_records_per_second"],
        exception_breakdown=data["exception_breakdown"],
        exception_summary=envelope.get("exception_summary"),
        exceptions=[ReconciliationResult.model_validate(e) for e in exceptions],
        errors=errors,
    )

    # Cache the response for identical deterministic inputs.
    reconciliation_cache.set(cache_key, response)

    return response


@router.get(
    "/reconcile/evaluation",
    response_model=ReconciliationEvaluationReport,
    summary="Evaluation-only: measured accuracy vs isolated ground truth",
    description=(
        "BENCHMARK/EVALUATION SURFACE. Runs the same seeded synthetic "
        "batch through the deterministic engine and then the isolated "
        "ground-truth evaluator, exposing measured accuracy, precision, "
        "recall, F1 and FP/FN. The serving endpoint (POST /reconcile) "
        "never sees ground truth and reports accuracy=null; only this "
        "explicitly-marked evaluation surface measures quality. "
        "Aggregate metrics and mismatch case ids are returned — never "
        "raw ground-truth records. No database, no LLM, no credentials."
    ),
)
def ai_reconcile_evaluation(
    seed: int = Query(default=42, ge=0, description="Generator seed"),
    size: int = Query(
        default=100,
        ge=50,
        le=5_000,
        description="Batch size in cases (Track 04 requires 50+)",
    ),
    max_settlement_delay_days: int = Query(
        default=DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
        ge=0,
        description="Settlement-delay tolerance in days",
    ),
) -> ReconciliationEvaluationReport:
    logger.info(
        "Reconciliation evaluation requested (seed=%d, size=%d)",
        seed,
        size,
    )

    # Check cache for previously computed evaluation results.
    cache_key = evaluation_cache_key(
        seed=seed,
        size=size,
        max_settlement_delay_days=max_settlement_delay_days,
    )
    cached = reconciliation_cache.get(cache_key)
    if cached is not None:
        logger.info("Evaluation cache hit (key=%s)", cache_key)
        return cached

    result = evaluate_batch_with_ground_truth(
        seed=seed,
        size=size,
        max_settlement_delay_days=max_settlement_delay_days,
    )
    reconciliation_cache.set(cache_key, result)
    return result


@router.post(
    "/reconcile/compare",
    response_model=AiReconcileCompareResponse,
    summary="Compare two reconciliation runs (What Changed / Drift Analysis)",
    description=(
        "Deterministically compares previous vs current reconciliation runs "
        "and computes match-rate change, exception-count change, exposure "
        "change, category drifts, priority distribution shifts, and major "
        "drivers. With `explain=true` an LLM narrative is attached describing "
        "the backend-computed facts."
    ),
)
def ai_reconcile_compare(
    payload: AiReconcileCompareRequest,
) -> AiReconcileCompareResponse:
    logger.info(
        "AI reconciliation comparison requested (prev_seed=%d, curr_seed=%d)",
        payload.previous_seed,
        payload.current_seed,
    )

    # Check cache for previously computed comparison results.
    cache_key = compare_cache_key(
        previous_seed=payload.previous_seed,
        previous_size=payload.previous_size,
        current_seed=payload.current_seed,
        current_size=payload.current_size,
        explain=payload.explain,
    )
    cached = reconciliation_cache.get(cache_key)
    if cached is not None:
        logger.info("Compare cache hit (key=%s)", cache_key)
        return cached

    prev_batch = generate_synthetic_batch(
        seed=payload.previous_seed, size=payload.previous_size
    )
    prev_report = build_report(
        prev_batch.payments, prev_batch.settlements, prev_batch.refunds,
        max_settlement_delay_days=payload.max_settlement_delay_days,
    )

    curr_batch = generate_synthetic_batch(
        seed=payload.current_seed, size=payload.current_size
    )
    curr_report = build_report(
        curr_batch.payments, curr_batch.settlements, curr_batch.refunds,
        max_settlement_delay_days=payload.max_settlement_delay_days,
    )

    drift = compare_runs(
        prev_report,
        curr_report,
        previous_seed=payload.previous_seed,
        current_seed=payload.current_seed,
    )

    errors: list[str] = []
    answer: str | None = None

    if payload.explain:
        try:
            facts = _drift_narration_signals(
                drift,
                prev_report.exception_summary,
                curr_report.exception_summary,
            )
            answer = _render_drift_narrative(facts)
        except Exception:
            logger.exception("Drift narration failed")
            errors.append(
                "The drift analysis completed, but the narrative explanation failed."
            )

    response = AiReconcileCompareResponse(
        drift=drift,
        previous_summary=prev_report.summary,
        current_summary=curr_report.summary,
        answer=_sanitize_llm_text(answer),
        errors=errors,
    )

    reconciliation_cache.set(cache_key, response)
    return response

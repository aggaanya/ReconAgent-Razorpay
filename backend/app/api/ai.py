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

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as OrmSession

from app.ai.graph.prompts import INTERPRETATION_SYSTEM_PROMPT, RECONCILIATION_EXPLAIN_SYSTEM_PROMPT
from app.ai.graph.service import FinanceAgentResult, FinanceIntelligenceAgent
from app.ai.llm import LLMError, LLMNotConfiguredError
from app.ai.tools import (
    RECONCILE_TRANSACTION_TOOL,
    FinanceToolError,
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
        answer=result.interpretation,
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


def _reconcile_signals(
    summary: dict, exceptions: list[dict], seed: int, size: int, exception_summary: dict | None = None
) -> dict:
    """Structured facts handed to the LLM (never ground truth).

    The payload is intentionally compact: only the fields the LLM needs
    to narrate the reconciliation outcome.  Raw exception records are NOT
    included — only aggregate counts, exposure, and a short sample.
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
        exposure_minor = exception_summary.get("total_financial_exposure_minor", 0)
        currency = exception_summary.get("exposure_currency") or "INR"
        signals["exposure_human_readable"] = (
            f"{currency} {exposure_minor / 100:,.2f}"
        )
    else:
        signals["exception_summary"] = None
        signals["exposure_human_readable"] = None

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
                entry["financial_impact_display"] = (
                    f"{exc.get('currency', 'INR')} {impact / 100:,.2f}"
                )
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
                    or "Explain this reconciliation outcome for a business "
                    "owner. Clarify that match rate is not accuracy. List "
                    "top exception categories with financial impact, "
                    "severity counts, unresolved items, and 2-3 recommended "
                    "actions for critical/high items. Be concise."
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
    return AiReconcileResponse(
        status=status,
        answer=answer,
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
    return evaluate_batch_with_ground_truth(
        seed=seed,
        size=size,
        max_settlement_delay_days=max_settlement_delay_days,
    )


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
    agent: FinanceIntelligenceAgent | None = None
    if payload.explain:
        agent = get_ai_agent()

    logger.info(
        "AI reconciliation comparison requested (prev_seed=%d, curr_seed=%d)",
        payload.previous_seed,
        payload.current_seed,
    )

    prev_batch = generate_synthetic_batch(
        seed=payload.previous_seed, size=payload.previous_size
    )
    prev_report = build_report(
        prev_batch.payments, prev_batch.settlements, prev_batch.refunds
    )

    curr_batch = generate_synthetic_batch(
        seed=payload.current_seed, size=payload.current_size
    )
    curr_report = build_report(
        curr_batch.payments, curr_batch.settlements, curr_batch.refunds
    )

    drift = compare_runs(
        prev_report,
        curr_report,
        previous_seed=payload.previous_seed,
        current_seed=payload.current_seed,
    )

    errors: list[str] = []
    answer: str | None = None

    if agent is not None:
        try:
            drift_facts = {
                "data": {
                    "drift": drift.model_dump(mode="json"),
                    "previous_summary": prev_report.summary.model_dump(mode="json"),
                    "current_summary": curr_report.summary.model_dump(mode="json"),
                }
            }
            reply = agent.llm_service.explain_signals(
                drift_facts,
                question=(
                    "Explain what changed between the previous and current reconciliation runs: "
                    "what is the match-rate trend, what drove exception/exposure shifts, "
                    "and what should the finance team focus on?"
                ),
                system_prompt=INTERPRETATION_SYSTEM_PROMPT,
            )
            answer = reply.content
        except LLMError as exc:
            logger.warning("Drift explanation failed (%s)", type(exc).__name__)
            errors.append(
                "The drift analysis completed, but the narrative explanation is unavailable."
            )

    return AiReconcileCompareResponse(
        drift=drift,
        previous_summary=prev_report.summary,
        current_summary=curr_report.summary,
        answer=answer,
        errors=errors,
    )

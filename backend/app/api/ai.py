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
  automatically after a reset (tests/lifespan), mirroring the Razorpay
  service pattern in ``app.api.razorpay``.
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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as OrmSession

from app.ai.graph.prompts import INTERPRETATION_SYSTEM_PROMPT
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
)
from app.schemas.reconciliation import (
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    ReconciliationEvaluationReport,
    ReconciliationResult,
)
from app.services.reconciliation import evaluate_batch_with_ground_truth

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
    summary: dict, exceptions: list[dict], seed: int, size: int
) -> dict:
    """Structured facts handed to the LLM (never ground truth)."""
    return {
        "data": {
            "summary": summary,
            "exceptions": exceptions,
        },
        "dataset": {
            "source": "synthetic",
            "seed": seed,
            "size": size,
        },
    }


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
                    data, exceptions, payload.seed, payload.size
                ),
                question=(
                    payload.question
                    or "Explain this reconciliation outcome for a business "
                    "owner: what matches, what went wrong, and what needs "
                    "human review."
                ),
                system_prompt=INTERPRETATION_SYSTEM_PROMPT,
            )
            answer = reply.content
        except LLMError as exc:
            logger.warning(
                "Reconciliation explanation failed (%s)", type(exc).__name__
            )
            errors.append(
                "The reconciliation completed, but the narrative "
                "explanation is unavailable."
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

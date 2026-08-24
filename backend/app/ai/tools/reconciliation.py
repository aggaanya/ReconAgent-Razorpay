"""Reconciliation tool: Track 04 finance-ops loop over a synthetic batch.

Wraps the deterministic reconciliation engine
(``app.services.reconciliation``) exactly the way every other tool wraps
a ``FinanceService`` method — validation, error sanitization, and a
JSON-safe envelope; **zero financial logic here**. The engine decides
every match/exception; this layer only selects and shapes its output.

Unlike the database-backed tools, this one needs no SQLAlchemy session:
its data source is the seeded synthetic dataset generator
(``app.services.reconciliation_synthetic``), so ``requires_session`` is
``False``. The LangGraph layer therefore cannot reach repositories or
sessions through it — the tool exposes only ``source``/``seed``/``size``
and returns the engine's report verbatim.
"""

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.tools.base import FinanceTool
from app.schemas.reconciliation import (
    DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
    ReconcileSourceName,
    ReconciliationReport,
)
from app.services.reconciliation import build_report
from app.services.reconciliation_synthetic import generate_synthetic_batch


class ReconcileToolInput(BaseModel):
    """Input for the reconcile-transactions tool.

    Only dataset selection lives here — never record contents. Callers
    cannot inject records (and thus cannot smuggle ground truth into the
    engine); the batch is always generated deterministically from the
    seed inside the tool.
    """

    model_config = {"extra": "forbid"}

    source: ReconcileSourceName = Field(
        default="synthetic",
        description="Dataset source; 'synthetic' is the Track 04 batch",
    )
    seed: int = Field(
        default=42,
        ge=0,
        description="Deterministic generator seed",
    )
    size: int = Field(
        default=100,
        ge=50,
        le=5_000,
        description="Number of cases in the batch (Track 04 requires 50+)",
    )
    max_settlement_delay_days: int = Field(
        default=DEFAULT_MAX_SETTLEMENT_DELAY_DAYS,
        ge=0,
        description=(
            "Settlement-delay tolerance in days; settlements arriving "
            "later are flagged SETTLEMENT_DELAY"
        ),
    )


class ReconcileTransactionsTool(FinanceTool):
    """Run the deterministic reconciliation engine over a synthetic batch."""

    name = "reconcile_transactions"
    description = (
        "Reconcile a deterministic batch of payment/settlement/refund "
        "records (default 100 cases, seeded) using fixed matching rules "
        "over expected settlement amounts (gross minus refunds, fees, "
        "and tax). Returns the summary metrics (match rate, counts, "
        "throughput), every decision with its amount components, and the "
        "exception list with exact minor-unit differences. Read-only "
        "evaluation over synthetic data; no LLM and no database involved."
    )
    input_model = ReconcileToolInput
    requires_session = False

    def _fetch(
        self, session: Session | None, params: BaseModel
    ) -> ReconciliationReport:
        assert isinstance(params, ReconcileToolInput)
        # ``session`` is deliberately unused (requires_session=False):
        # the dataset is generated deterministically from the seed.
        batch = generate_synthetic_batch(seed=params.seed, size=params.size)
        return build_report(
            batch.payments,
            batch.settlements,
            batch.refunds,
            max_settlement_delay_days=params.max_settlement_delay_days,
        )

    def _render(self, result: ReconciliationReport) -> dict[str, Any]:
        return {
            "dataset": {
                "source": "synthetic",
                "note": (
                    "Deterministic synthetic batch; amounts are exact "
                    "minor-unit integers."
                ),
            },
            "data": result.summary.model_dump(mode="json"),
            "exception_summary": (
                result.exception_summary.model_dump(mode="json")
                if result.exception_summary
                else None
            ),
            "exceptions": [
                exception.model_dump(mode="json")
                for exception in result.exceptions
            ],
            "results": [
                item.model_dump(mode="json") for item in result.results
            ],
        }

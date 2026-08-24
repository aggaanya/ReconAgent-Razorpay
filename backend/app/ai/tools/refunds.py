"""Refund tool: the engine's Part-4 refund metrics for one period.

Refund aggregates already live inside ``FinanceService.revenue`` (spec
Parts 3-4 are defined over the same window), so this tool reuses that
single call and surfaces only the refund-relevant fields. No separate
refund aggregation is implemented here — that would duplicate the engine.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.tools.base import FinanceTool, resolve_named_period
from app.ai.tools.inputs import RefundToolInput
from app.schemas.finance import RevenueMetricsResponse
from app.services.metrics import FinanceService


class RefundTool(FinanceTool):
    """Return refund count/amount/rate plus the gross-revenue denominator.

    ``gross_revenue_minor`` is included because ``refund_rate`` is defined
    (spec §4.3 definition A) against it; both values are echoed verbatim
    from the engine so the LLM can explain the rate without recomputing it.
    """

    name = "refunds"
    description = (
        "Refund metrics (processed-only count, amount, and rate) for a "
        "named reporting period and single currency, with the gross "
        "revenue denominator the rate is defined against."
    )
    input_model = RefundToolInput

    def _fetch(self, session: Session, params: BaseModel) -> RevenueMetricsResponse:
        assert isinstance(params, RefundToolInput)
        start, end = resolve_named_period(params.period)
        return FinanceService.revenue(
            session,
            start=start,
            end=end,
            currency=params.currency,
        )

    def _render(self, result: RevenueMetricsResponse) -> dict[str, Any]:
        return {
            "period": result.period.model_dump(mode="json"),
            "data": {
                **result.refunds.model_dump(mode="json"),
                "gross_revenue_minor": result.gross_revenue_minor,
            },
        }

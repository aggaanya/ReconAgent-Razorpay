"""Revenue tool: exposes the engine's Part-3 revenue metrics verbatim."""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.tools.base import FinanceTool, resolve_named_period
from app.ai.tools.inputs import RevenueToolInput
from app.schemas.finance import RevenueMetricsResponse
from app.services.metrics import FinanceService


class RevenueTool(FinanceTool):
    """Return existing revenue metrics for one named period + currency.

    Data is ``FinanceService.revenue`` output unchanged: gross revenue,
    fee/tax sums, the three labeled net variants, and the processed-only
    refund block (docs/FINANCE_METRICS_SPECIFICATION.md Parts 3-4).
    """

    name = "revenue"
    description = (
        "Deterministic revenue metrics (gross, fees, taxes, net variants, "
        "refunds) for a named reporting period and single currency, "
        "computed by the Finance Intelligence Engine."
    )
    input_model = RevenueToolInput

    def _fetch(self, session: Session, params: BaseModel) -> RevenueMetricsResponse:
        assert isinstance(params, RevenueToolInput)
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
            "data": result.model_dump(mode="json", exclude={"period"}),
        }

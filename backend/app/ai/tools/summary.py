"""Financial summary tool: the engine's full multi-currency overview.

Returns ``FinanceService.summary`` output verbatim: gross/successful/
failed/in-progress counts, fee/tax/refund sums, all three net variants,
and settlement totals, grouped by currency over an inclusive UTC date
window with optional filters.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.tools.base import FinanceTool
from app.ai.tools.inputs import FinancialSummaryToolInput
from app.schemas.finance import FinanceSummaryResponse
from app.services.metrics import FinanceService


class FinancialSummaryTool(FinanceTool):
    """Overall structured financial summary across currencies."""

    name = "financial_summary"
    description = (
        "Overall financial summary per currency (gross revenue, counts, "
        "fees, taxes, refunds, net variants, settlements) over an "
        "inclusive UTC date window with optional filters."
    )
    input_model = FinancialSummaryToolInput

    def _fetch(self, session: Session, params: BaseModel) -> FinanceSummaryResponse:
        assert isinstance(params, FinancialSummaryToolInput)
        return FinanceService.summary(
            session,
            start_date=params.start_date,
            end_date=params.end_date,
            currency=params.currency,
            status=params.status,
            method=params.method,
        )

    def _render(self, result: FinanceSummaryResponse) -> dict[str, Any]:
        return {
            "window": {
                "start_date": result.start_date.isoformat()
                if result.start_date is not None
                else None,
                "end_date": result.end_date.isoformat()
                if result.end_date is not None
                else None,
                "timezone": result.timezone,
            },
            "filters": {
                "currency": result.currency,
                "status": result.status,
                "method": result.method,
            },
            "data": {
                "currencies": [
                    item.model_dump(mode="json") for item in result.currencies
                ]
            },
        }

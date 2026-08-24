"""Settlements tool: engine's §5.2 settlement aggregates per currency.

Settlement aggregates are computed by ``FinanceService.summary`` (the
same numbers the finance API exposes); this tool projects only the
settlement fields out of each currency group. Projection is field
selection, not calculation — every value is echoed unchanged.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.tools.base import FinanceTool
from app.ai.tools.inputs import SettlementToolInput
from app.schemas.finance import FinanceSummaryResponse
from app.services.metrics import FinanceService


class SettlementTool(FinanceTool):
    """Return settlement amount/fees/tax aggregates for a date window."""

    name = "settlements"
    description = (
        "Settlement aggregates (amount, fees, tax in exact minor units) "
        "per currency over an inclusive UTC date window, as aggregated by "
        "the Finance Intelligence Engine."
    )
    input_model = SettlementToolInput

    def _fetch(self, session: Session, params: BaseModel) -> FinanceSummaryResponse:
        assert isinstance(params, SettlementToolInput)
        return FinanceService.summary(
            session,
            start_date=params.start_date,
            end_date=params.end_date,
            currency=params.currency,
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
            "filters": {"currency": result.currency},
            "data": {
                "currencies": [
                    {
                        "currency": item.currency,
                        "settlement_amount_minor": item.settlement_amount_minor,
                        "settlement_fees_minor": item.settlement_fees_minor,
                        "settlement_tax_minor": item.settlement_tax_minor,
                    }
                    for item in result.currencies
                ]
            },
        }

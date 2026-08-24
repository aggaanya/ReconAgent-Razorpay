"""Trend tool: the engine's Part-6 period-over-period comparison."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.tools.base import FinanceTool
from app.ai.tools.inputs import TrendToolInput
from app.core.periods import REPORTING_TIMEZONE
from app.schemas.finance import TrendResponse
from app.services.metrics import FinanceService


class TrendTool(FinanceTool):
    """Compare one supported metric across current/previous windows.

    Metric vocabulary, window math, and change semantics all come from
    ``FinanceService.trend`` (spec Part 6); this tool only forwards the
    caller's metric/granularity/currency choice and echoes the result.
    """

    name = "trends"
    description = (
        "Period-over-period trend for one finance metric (transaction "
        "volume, gross revenue, refunds, settlements, ...) comparing "
        "current vs previous day/week/month windows in IST."
    )
    input_model = TrendToolInput

    def _fetch(self, session: Session, params: BaseModel) -> TrendResponse:
        assert isinstance(params, TrendToolInput)
        return FinanceService.trend(
            session,
            metric=params.metric,
            granularity=params.granularity,
            now=datetime.now(tz=REPORTING_TIMEZONE),
            currency=params.currency,
        )

    def _render(self, result: TrendResponse) -> dict[str, Any]:
        return {"data": result.model_dump(mode="json")}

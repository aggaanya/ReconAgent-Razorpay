"""Payment-performance tool: engine's Part-2 transaction health metrics."""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.tools.base import FinanceTool, resolve_named_period
from app.ai.tools.inputs import PaymentPerformanceToolInput
from app.schemas.finance import PaymentPerformanceResponse
from app.services.metrics import FinanceService


class PaymentPerformanceTool(FinanceTool):
    """Return existing payment performance metrics verbatim.

    Transaction volume, successful/failed/in-progress counts, success and
    failure rates (``None`` when undefined) — exactly as computed by the
    Finance Intelligence Engine for one named period and currency.
    """

    name = "payment_performance"
    description = (
        "Payment performance metrics: transaction volume, successful/"
        "failed/in-progress counts and success/failure rates for a named "
        "reporting period and single currency."
    )
    input_model = PaymentPerformanceToolInput

    def _fetch(
        self, session: Session, params: BaseModel
    ) -> PaymentPerformanceResponse:
        assert isinstance(params, PaymentPerformanceToolInput)
        start, end = resolve_named_period(params.period)
        return FinanceService.payment_performance(
            session,
            start=start,
            end=end,
            currency=params.currency,
        )

    def _render(self, result: PaymentPerformanceResponse) -> dict[str, Any]:
        return {
            "period": result.period.model_dump(mode="json"),
            "data": result.model_dump(mode="json", exclude={"period"}),
        }

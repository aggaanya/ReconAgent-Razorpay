"""Normalized refund records.

Money rules (explicit, tested):

- ``amount_minor`` is the exact integer refunded amount in the currency's
  smallest unit, preserved verbatim. No floating point anywhere.

Refunds reference their parent payment by provider id; the link is stored
as an indexed column (no hard FK: a refund may arrive before its payment
has been synced). Razorpay refunds carry no fee/tax fields.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NormalizedRefund(BaseModel):
    """Provider-independent refund record for downstream consumption."""

    provider: Literal["razorpay"]
    external_id: str = Field(description="Provider refund id, preserved exactly")
    entity: str | None = None
    razorpay_payment_id: str = Field(
        description="Provider id of the refunded payment"
    )
    amount_minor: int = Field(description="Exact smallest-unit refunded amount")
    currency: str | None = None
    status: str | None = None
    speed: str | None = Field(
        default=None, description="Payout speed: normal | optimum (when provided)"
    )
    notes: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = Field(
        default=None, description="UTC timestamp of provider creation time"
    )


class RefundListResponse(BaseModel):
    """One bounded page of refunds."""

    items: list[NormalizedRefund]
    count: int = Field(ge=0, description="Number of items in this page")
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

"""Normalized order records.

Money rules (explicit, tested):

- ``amount_minor``/``amount_paid_minor``/``amount_due_minor`` are exact
  integers in the currency's smallest unit (paise for INR), preserved
  verbatim from the provider. No floating point anywhere.

Razorpay orders carry no fee/tax and no payment references beyond the
receipt the merchant supplied; nothing is invented here.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NormalizedOrder(BaseModel):
    """Provider-independent order record for downstream consumption."""

    provider: Literal["razorpay"]
    external_id: str = Field(description="Provider order id, preserved exactly")
    entity: str | None = None
    amount_minor: int = Field(description="Exact smallest-unit order value")
    amount_paid_minor: int = Field(default=0, description="Smallest-unit paid portion")
    amount_due_minor: int = Field(default=0, description="Smallest-unit due portion")
    currency: str | None = None
    status: str | None = None
    receipt: str | None = Field(
        default=None, description="Merchant-supplied receipt reference"
    )
    notes: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = Field(
        default=None, description="UTC timestamp of provider creation time"
    )


class OrderListResponse(BaseModel):
    """One bounded page of orders."""

    items: list[NormalizedOrder]
    count: int = Field(ge=0, description="Number of items in this page")
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

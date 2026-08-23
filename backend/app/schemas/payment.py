"""Normalized payment records.

Money rules (explicit, tested):

- ``amount_minor`` is the exact integer amount in the currency's smallest
  unit (paise for INR), preserved verbatim from the provider. No floating
  point is used anywhere in this module.
- Conversion to major units is intentionally deferred to the consumers
  that need it; this schema never loses precision.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NormalizedPayment(BaseModel):
    """Provider-independent payment record for downstream consumption."""

    provider: Literal["razorpay"]
    external_id: str = Field(description="Provider payment id, preserved exactly")
    entity: str | None = None
    amount_minor: int = Field(description="Exact smallest-unit amount (e.g. paise)")
    currency: str | None = None
    status: str | None = None
    order_id: str | None = None
    invoice_id: str | None = None
    method: str | None = None
    captured: bool | None = None
    # Platform fee/tax in exact minor units; None when the provider has not
    # levied them yet (typically uncaptured payments).
    fee_minor: int | None = Field(default=None, description="Provider fee (minor units)")
    tax_minor: int | None = Field(default=None, description="Fee tax (minor units)")
    description: str | None = None
    notes: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = Field(
        default=None, description="UTC timestamp of provider creation time"
    )


class PaymentListResponse(BaseModel):
    """One bounded page of payments."""

    items: list[NormalizedPayment]
    count: int = Field(ge=0, description="Number of items in this page")
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

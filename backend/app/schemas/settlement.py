"""Normalized settlement records.

Money rules (explicit, tested):

- ``amount_minor``/``fees_minor``/``tax_minor`` are exact integers in the
  currency's smallest unit (paise for INR), preserved verbatim from the
  provider. No floating point is used anywhere in this module.
- The documented Razorpay settlement entity carries no currency field;
  ``currency`` is surfaced only when the provider includes one.

Payment/order linkage is intentionally absent: Razorpay's settlement
entity has no such references. The link exists on the payment side
(``settlement_id``) and will be modeled in a later phase.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NormalizedSettlement(BaseModel):
    """Provider-independent settlement record for downstream consumption."""

    provider: Literal["razorpay"]
    external_id: str = Field(description="Provider settlement id, preserved exactly")
    entity: str | None = None
    amount_minor: int = Field(description="Exact smallest-unit settled amount")
    status: str | None = None
    fees_minor: int = Field(default=0, description="Exact smallest-unit fees")
    tax_minor: int = Field(default=0, description="Exact smallest-unit tax on fees")
    utr: str | None = Field(
        default=None,
        description="Bank Unique Transaction Reference for the settlement",
    )
    currency: str | None = None
    created_at: datetime | None = Field(
        default=None, description="UTC timestamp of provider creation time"
    )


class SettlementListResponse(BaseModel):
    """One bounded page of settlements."""

    items: list[NormalizedSettlement]
    count: int = Field(ge=0, description="Number of items in this page")
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

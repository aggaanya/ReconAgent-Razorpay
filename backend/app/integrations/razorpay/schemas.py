"""Typed models for the subset of Razorpay payment payloads we consume.

These are wire models: they mirror Razorpay's response shape (subset, never
the full surface) and are deliberately kept inside the integration package.
Everything crossing into the application is normalized by the service layer.

Razorpay omits or nulls fields depending on resource state, so every field
except ``id``/``amount`` is optional; unknown fields are ignored so new
provider additions cannot break parsing.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .exceptions import RazorpayError, RazorpayInvalidResponseError


class RazorpayPayment(BaseModel):
    """One item of a ``GET /payments`` collection."""

    model_config = ConfigDict(extra="ignore")

    id: str
    entity: str | None = None
    amount: int
    currency: str | None = None
    status: str | None = None
    order_id: str | None = None
    invoice_id: str | None = None
    method: str | None = None
    captured: bool | None = None
    # Platform fee and tax, minor units — present on captured payments,
    # absent/null otherwise, hence optional.
    fee: int | None = None
    tax: int | None = None
    description: str | None = None
    notes: dict[str, str] = {}
    created_at: int | None = None


class RazorpayPaymentCollection(BaseModel):
    """Envelope of ``GET /payments``: ``{"entity": "collection", ...}``."""

    model_config = ConfigDict(extra="ignore")

    entity: str | None = None
    count: int = 0
    items: list[RazorpayPayment]


def parse_payment_collection(payload: Any) -> RazorpayPaymentCollection:
    """Validate an HTTP body as a payments collection.

    Raises ``RazorpayInvalidResponseError`` (a ``RazorpayError`` subclass,
    never pydantic errors) on any unexpected shape so callers only deal
    with typed integration failures.
    """
    if not isinstance(payload, dict):
        raise RazorpayInvalidResponseError(
            "Unexpected Razorpay payments payload: expected a JSON object"
        )
    try:
        return RazorpayPaymentCollection.model_validate(payload)
    except ValidationError as exc:
        raise RazorpayInvalidResponseError(
            "Malformed Razorpay payments payload: items did not match the "
            "expected payment schema"
        ) from exc


class RazorpaySettlement(BaseModel):
    """One item of a ``GET /settlements`` collection.

    Field set mirrors Razorpay's documented Settlements entity exactly
    (id/entity/amount/status/fees/tax/utr/created_at). ``currency`` is not
    part of the documented entity but is tolerated when present so the
    parser never breaks on provider additions.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    entity: str | None = None
    amount: int
    status: str | None = None
    fees: int = 0
    tax: int = 0
    utr: str | None = None
    currency: str | None = None
    created_at: int | None = None


class RazorpaySettlementCollection(BaseModel):
    """Envelope of ``GET /settlements``."""

    model_config = ConfigDict(extra="ignore")

    entity: str | None = None
    count: int = 0
    items: list[RazorpaySettlement]


def parse_settlement_collection(payload: Any) -> RazorpaySettlementCollection:
    """Validate an HTTP body as a settlements collection (typed failures)."""
    if not isinstance(payload, dict):
        raise RazorpayInvalidResponseError(
            "Unexpected Razorpay settlements payload: expected a JSON object"
        )
    try:
        return RazorpaySettlementCollection.model_validate(payload)
    except ValidationError as exc:
        raise RazorpayInvalidResponseError(
            "Malformed Razorpay settlements payload: items did not match "
            "the expected settlement schema"
        ) from exc


class RazorpayOrder(BaseModel):
    """One item of a ``GET /orders`` collection.

    Field set mirrors Razorpay's documented Order entity subset we consume
    (id/entity/amount/amount_paid/amount_due/currency/receipt/status/
    notes/created_at). Unknown fields are ignored.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    entity: str | None = None
    amount: int
    amount_paid: int = 0
    amount_due: int = 0
    currency: str | None = None
    receipt: str | None = None
    status: str | None = None
    notes: dict[str, str] = {}
    created_at: int | None = None


class RazorpayOrderCollection(BaseModel):
    """Envelope of ``GET /orders``."""

    model_config = ConfigDict(extra="ignore")

    entity: str | None = None
    count: int = 0
    items: list[RazorpayOrder]


def parse_order_collection(payload: Any) -> RazorpayOrderCollection:
    """Validate an HTTP body as an orders collection (typed failures)."""
    if not isinstance(payload, dict):
        raise RazorpayInvalidResponseError(
            "Unexpected Razorpay orders payload: expected a JSON object"
        )
    try:
        return RazorpayOrderCollection.model_validate(payload)
    except ValidationError as exc:
        raise RazorpayInvalidResponseError(
            "Malformed Razorpay orders payload: items did not match the "
            "expected order schema"
        ) from exc


class RazorpayRefund(BaseModel):
    """One item of a ``GET /refunds`` collection.

    Field set mirrors Razorpay's documented Refund entity subset we consume
    (id/entity/payment_id/amount/currency/status/speed/notes/created_at).
    Unknown fields are ignored; ``speed`` defaults to the provider's
    ``normal`` when omitted.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    entity: str | None = None
    payment_id: str
    amount: int
    currency: str | None = None
    status: str | None = None
    speed: str | None = None
    notes: dict[str, str] = {}
    created_at: int | None = None


class RazorpayRefundCollection(BaseModel):
    """Envelope of ``GET /refunds``."""

    model_config = ConfigDict(extra="ignore")

    entity: str | None = None
    count: int = 0
    items: list[RazorpayRefund]


def parse_refund_collection(payload: Any) -> RazorpayRefundCollection:
    """Validate an HTTP body as a refunds collection (typed failures)."""
    if not isinstance(payload, dict):
        raise RazorpayInvalidResponseError(
            "Unexpected Razorpay refunds payload: expected a JSON object"
        )
    try:
        return RazorpayRefundCollection.model_validate(payload)
    except ValidationError as exc:
        raise RazorpayInvalidResponseError(
            "Malformed Razorpay refunds payload: items did not match the "
            "expected refund schema"
        ) from exc

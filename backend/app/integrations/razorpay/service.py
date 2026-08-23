"""Application-level Razorpay service.

Sits between FastAPI routers and ``RazorpayClient``:

- translates Razorpay wire payloads into normalized application schemas
  (``app.schemas``), so nothing above this layer depends on Razorpay's
  response structure, URL layout, or pagination mechanics;
- keeps provider failures typed (``RazorpayError`` hierarchy) for clean
  HTTP translation at the API boundary.

Chunk scope: payments and settlements (both read-only). Refunds will
extend this class.
"""

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.schemas import (
    NormalizedOrder,
    NormalizedPayment,
    NormalizedRefund,
    NormalizedSettlement,
    OrderListResponse,
    PaymentListResponse,
    RefundListResponse,
    SettlementListResponse,
)

from .client import RazorpayClient
from .exceptions import RazorpayError
from .schemas import (
    RazorpayOrder,
    RazorpayPayment,
    RazorpayRefund,
    RazorpaySettlement,
    parse_order_collection,
    parse_payment_collection,
    parse_refund_collection,
    parse_settlement_collection,
)

# Razorpay allows at most 100 records per page; we additionally bound the
# offset so a single request can never walk an unbounded result set.
MAX_PAGE_SIZE = 100
MAX_SKIP = 10_000
DEFAULT_PAGE_SIZE = 10

# Connection verification fetches the smallest possible authenticated page.
VERIFY_PAGE_SIZE = 1

# Razorpay validates from/to Unix timestamps to this window on list
# endpoints (2000-01-01T00:00:00Z .. 2121-01-01T00:00:00Z); enforcing it
# locally yields a clean 422 instead of an upstream 400 round-trip.
EPOCH_MIN = 946_684_800
EPOCH_MAX = 4_765_046_400


def _utc_from_epoch(epoch_seconds: int | None) -> datetime | None:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


@dataclass(frozen=True)
class ConnectionProbeResult:
    """Outcome of a credential/connectivity verification probe.

    ``environment`` reflects the configured key mode (``test``/``live``/
    ``unknown``) — never any part of the credentials themselves.
    """

    connected: bool
    environment: str
    items_seen: int = 0


class RazorpayService:
    """Read-only application service over the Razorpay API."""

    def __init__(self, client: RazorpayClient) -> None:
        self._client = client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        **client_overrides: Any,
    ) -> "RazorpayService":
        return cls(
            RazorpayClient.from_settings(settings, **client_overrides)
        )

    @property
    def client(self) -> RazorpayClient:
        return self._client

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RazorpayService":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- connection verification -----------------------------------------

    def verify_connection(self) -> ConnectionProbeResult:
        """Probe Razorpay with the smallest authenticated request.

        Performs ``GET /payments?count=1`` — cheap, read-only, and only
        succeeds when Basic Auth credentials are accepted, so it verifies
        both connectivity and credential validity in one round-trip.

        Returns a :class:`ConnectionProbeResult` on success; raises typed
        ``RazorpayError`` subclasses (auth/rate-limit/network/server/
        invalid-response) that callers translate into structured failures.
        Raw payloads are parsed and discarded — nothing provider-specific
        beyond counts is surfaced.
        """
        payload = self._client.get(
            "payments", params={"count": VERIFY_PAGE_SIZE, "skip": 0}
        )
        collection = parse_payment_collection(payload)
        return ConnectionProbeResult(
            connected=True,
            environment=self._client.key_mode,
            items_seen=len(collection.items),
        )

    # --- payments ------------------------------------------------------

    def list_payments(
        self,
        *,
        count: int = DEFAULT_PAGE_SIZE,
        skip: int = 0,
        from_epoch: int | None = None,
        to_epoch: int | None = None,
    ) -> PaymentListResponse:
        """Fetch one bounded page of payments, newest first (provider order).

        ``count``/``skip`` map to Razorpay pagination; ``from_epoch``/
        ``to_epoch`` map to the provider's inclusive ``from``/``to`` Unix
        second filters and are omitted when not supplied.
        """
        params = self._page_params(
            count=count, skip=skip, from_epoch=from_epoch, to_epoch=to_epoch
        )
        payload = self._client.get("payments", params=params)
        collection = parse_payment_collection(payload)

        items = [self._normalize(payment) for payment in collection.items]
        return PaymentListResponse(
            items=items,
            count=len(items),
            limit=count,
            offset=skip,
        )

    @staticmethod
    def _normalize(payment: RazorpayPayment) -> NormalizedPayment:
        """Map wire model -> provider-independent schema (exact ids/amounts)."""
        return NormalizedPayment(
            provider="razorpay",
            external_id=payment.id,
            entity=payment.entity,
            amount_minor=payment.amount,
            currency=payment.currency,
            status=payment.status,
            order_id=payment.order_id,
            invoice_id=payment.invoice_id,
            method=payment.method,
            captured=payment.captured,
            fee_minor=payment.fee,
            tax_minor=payment.tax,
            description=payment.description,
            notes=payment.notes,
            created_at=_utc_from_epoch(payment.created_at),
        )

    # --- orders ---------------------------------------------------------

    def list_orders(
        self,
        *,
        count: int = DEFAULT_PAGE_SIZE,
        skip: int = 0,
    ) -> OrderListResponse:
        """Fetch one bounded page of orders (provider order).

        Razorpay documents only ``count``/``skip`` pagination for
        ``GET /orders`` (no ``from``/``to`` filters), so no date arguments
        are accepted here — nothing is fabricated.
        """
        params = self._page_params(count=count, skip=skip, from_epoch=None, to_epoch=None)
        payload = self._client.get("orders", params=params)
        collection = parse_order_collection(payload)

        items = [self._normalize_order(order) for order in collection.items]
        return OrderListResponse(
            items=items, count=len(items), limit=count, offset=skip
        )

    @staticmethod
    def _normalize_order(order: RazorpayOrder) -> NormalizedOrder:
        """Map wire model -> provider-independent schema (exact ids/amounts)."""
        return NormalizedOrder(
            provider="razorpay",
            external_id=order.id,
            entity=order.entity,
            amount_minor=order.amount,
            amount_paid_minor=order.amount_paid,
            amount_due_minor=order.amount_due,
            currency=order.currency,
            status=order.status,
            receipt=order.receipt,
            notes=order.notes,
            created_at=_utc_from_epoch(order.created_at),
        )

    # --- refunds ----------------------------------------------------------

    def list_refunds(
        self,
        *,
        count: int = DEFAULT_PAGE_SIZE,
        skip: int = 0,
    ) -> RefundListResponse:
        """Fetch one bounded page of refunds (provider order).

        Like orders, ``GET /refunds`` documents only ``count``/``skip``
        pagination; date filters are not part of the contract.
        """
        params = self._page_params(count=count, skip=skip, from_epoch=None, to_epoch=None)
        payload = self._client.get("refunds", params=params)
        collection = parse_refund_collection(payload)

        items = [self._normalize_refund(refund) for refund in collection.items]
        return RefundListResponse(
            items=items, count=len(items), limit=count, offset=skip
        )

    @staticmethod
    def _normalize_refund(refund: RazorpayRefund) -> NormalizedRefund:
        """Map wire model -> provider-independent schema (exact ids/amounts)."""
        return NormalizedRefund(
            provider="razorpay",
            external_id=refund.id,
            entity=refund.entity,
            razorpay_payment_id=refund.payment_id,
            amount_minor=refund.amount,
            currency=refund.currency,
            status=refund.status,
            speed=refund.speed,
            notes=refund.notes,
            created_at=_utc_from_epoch(refund.created_at),
        )

    # --- settlements ---------------------------------------------------

    def list_settlements(
        self,
        *,
        count: int = DEFAULT_PAGE_SIZE,
        skip: int = 0,
        from_epoch: int | None = None,
        to_epoch: int | None = None,
    ) -> SettlementListResponse:
        """Fetch one bounded page of settlements (provider order).

        Same pagination/filter contract as payments: Razorpay documents
        ``count`` (1-100), ``skip``, and inclusive ``from``/``to`` Unix
        second filters on ``GET /settlements``.
        """
        params = self._page_params(
            count=count, skip=skip, from_epoch=from_epoch, to_epoch=to_epoch
        )
        payload = self._client.get("settlements", params=params)
        collection = parse_settlement_collection(payload)

        items = [self._normalize_settlement(item) for item in collection.items]
        return SettlementListResponse(
            items=items,
            count=len(items),
            limit=count,
            offset=skip,
        )

    @staticmethod
    def _normalize_settlement(settlement: RazorpaySettlement) -> NormalizedSettlement:
        """Map wire model -> provider-independent schema (exact ids/amounts)."""
        return NormalizedSettlement(
            provider="razorpay",
            external_id=settlement.id,
            entity=settlement.entity,
            amount_minor=settlement.amount,
            status=settlement.status,
            fees_minor=settlement.fees,
            tax_minor=settlement.tax,
            utr=settlement.utr,
            currency=settlement.currency,
            created_at=_utc_from_epoch(settlement.created_at),
        )

    # --- shared helpers --------------------------------------------------

    @staticmethod
    def _page_params(
        *,
        count: int,
        skip: int,
        from_epoch: int | None,
        to_epoch: int | None,
    ) -> dict[str, Any]:
        """Validate and build the provider pagination/filter query."""
        if not 1 <= count <= MAX_PAGE_SIZE:
            raise RazorpayError(f"count must be between 1 and {MAX_PAGE_SIZE}")
        if not 0 <= skip <= MAX_SKIP:
            raise RazorpayError(f"skip must be between 0 and {MAX_SKIP}")
        if from_epoch is not None and not EPOCH_MIN <= from_epoch <= EPOCH_MAX:
            raise RazorpayError(
                f"from_epoch must be between {EPOCH_MIN} and {EPOCH_MAX}"
            )
        if to_epoch is not None and not EPOCH_MIN <= to_epoch <= EPOCH_MAX:
            raise RazorpayError(f"to_epoch must be between {EPOCH_MIN} and {EPOCH_MAX}")

        params: dict[str, Any] = {"count": count, "skip": skip}
        if from_epoch is not None:
            params["from"] = from_epoch
        if to_epoch is not None:
            params["to"] = to_epoch
        return params


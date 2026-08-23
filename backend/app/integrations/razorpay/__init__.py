"""Razorpay integration package.

Public surface:

- ``RazorpayClient`` — reusable HTTP transport (Basic Auth, timeouts,
  bounded retries, typed errors).
- ``RazorpayError`` hierarchy — provider failures mapped to application
  exceptions; safe to surface without credential leakage.

Wire-format parsing and normalization into internal schemas live above this
package in the service layer.
"""

from .client import RazorpayClient
from .constants import (
    DEFAULT_MAX_RETRIES,
    RETRY_BACKOFF_MULTIPLIER,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    detect_key_mode,
)
from .service import (
    DEFAULT_PAGE_SIZE,
    EPOCH_MAX,
    EPOCH_MIN,
    MAX_PAGE_SIZE,
    MAX_SKIP,
)
from .exceptions import (
    RazorpayAuthenticationError,
    RazorpayConnectionError,
    RazorpayError,
    RazorpayErrorDetail,
    RazorpayInvalidResponseError,
    RazorpayNotFoundError,
    RazorpayRateLimitError,
    RazorpayServerError,
    RazorpayValidationError,
)
from .schemas import (
    RazorpayOrder,
    RazorpayOrderCollection,
    RazorpayPayment,
    RazorpayPaymentCollection,
    RazorpayRefund,
    RazorpayRefundCollection,
    RazorpaySettlement,
    RazorpaySettlementCollection,
    parse_order_collection,
    parse_payment_collection,
    parse_refund_collection,
    parse_settlement_collection,
)
from .service import RazorpayService

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_PAGE_SIZE",
    "EPOCH_MAX",
    "EPOCH_MIN",
    "MAX_PAGE_SIZE",
    "MAX_SKIP",
    "RETRY_BACKOFF_MULTIPLIER",
    "RETRY_BASE_DELAY_SECONDS",
    "RETRY_MAX_DELAY_SECONDS",
    "RazorpayAuthenticationError",
    "RazorpayClient",
    "RazorpayConnectionError",
    "RazorpayError",
    "RazorpayErrorDetail",
    "RazorpayInvalidResponseError",
    "RazorpayNotFoundError",
    "RazorpayOrder",
    "RazorpayOrderCollection",
    "RazorpayPayment",
    "RazorpayPaymentCollection",
    "RazorpayRateLimitError",
    "RazorpayRefund",
    "RazorpayRefundCollection",
    "RazorpayServerError",
    "RazorpayService",
    "RazorpaySettlement",
    "RazorpaySettlementCollection",
    "RazorpayValidationError",
    "detect_key_mode",
    "parse_order_collection",
    "parse_payment_collection",
    "parse_refund_collection",
    "parse_settlement_collection",
]

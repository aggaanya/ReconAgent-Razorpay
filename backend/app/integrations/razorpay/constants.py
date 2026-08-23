"""Non-secret Razorpay integration tunables.

Retry/backoff values are deliberately conservative defaults; operational
bounds (max retries via configuration, capped delay here) guarantee that
every request path terminates.
"""

DEFAULT_MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 0.5
RETRY_BACKOFF_MULTIPLIER = 2.0
RETRY_MAX_DELAY_SECONDS = 30.0

# A dedicated connect timeout bounds fail-fast behaviour even when the
# configured read timeout is generous (e.g. slow paginated exports).
CONNECT_TIMEOUT_CAP_SECONDS = 5.0

# HTTP statuses considered transient by Razorpay's API contract: rate
# limiting plus any server-side failure class.
RATE_LIMIT_STATUS = 429
SERVER_ERROR_STATUS_MIN = 500


def is_transient_status(status_code: int) -> bool:
    """Whether an HTTP status represents a retryable transient failure."""
    return (
        status_code == RATE_LIMIT_STATUS
        or SERVER_ERROR_STATUS_MIN <= status_code <= 599
    )


# Razorpay key ids encode the credential mode as a prefix. Deriving only the
# mode (never echoing the id itself) is safe for API responses.
TEST_KEY_PREFIX = "rzp_test_"
LIVE_KEY_PREFIX = "rzp_live_"


def detect_key_mode(key_id: str | None) -> str:
    """Classify a key id as ``test``/``live``/``unknown`` — never log it."""
    if not key_id:
        return "unknown"
    if key_id.startswith(TEST_KEY_PREFIX):
        return "test"
    if key_id.startswith(LIVE_KEY_PREFIX):
        return "live"
    return "unknown"

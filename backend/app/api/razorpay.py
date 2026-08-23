"""Internal read-only Razorpay API.

Conventions:

- Prefix ``/api/v1/razorpay``; resources are exposed as normalized
  application schemas, never raw provider payloads.
- Query parameters are validated locally (FastAPI constraints plus the
  provider's documented epoch window) so bad input yields a clean 422
  without an upstream round-trip.
- ``RazorpayError`` subclasses are translated to safe HTTP responses;
  provider descriptions are surfaced only through sanitized messages and
  never include credentials or stack traces.
- The service is built once per process (pooled HTTP client) and rebuilt
  automatically if settings change after a reset (tests/lifespan).
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.integrations.razorpay import (
    MAX_PAGE_SIZE,
    MAX_SKIP,
    DEFAULT_PAGE_SIZE,
    EPOCH_MAX,
    EPOCH_MIN,
    RazorpayAuthenticationError,
    RazorpayConnectionError,
    RazorpayError,
    RazorpayInvalidResponseError,
    RazorpayNotFoundError,
    RazorpayRateLimitError,
    RazorpayServerError,
    RazorpayService,
    RazorpayValidationError,
    detect_key_mode,
)
from app.schemas import (
    ConnectionErrorDetail,
    ConnectionStatusResponse,
    PaymentListResponse,
    SettlementListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/razorpay", tags=["razorpay"])

_service: RazorpayService | None = None


def get_razorpay_service() -> RazorpayService:
    """FastAPI dependency returning the process-wide service instance."""
    global _service
    if _service is None:
        try:
            _service = RazorpayService.from_settings(get_settings())
        except RazorpayError as exc:
            # Missing credentials: clear 503 instead of an opaque failure.
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _service


def reset_razorpay_service() -> None:
    """Close and discard the cached service (lifespan shutdown / tests)."""
    global _service
    if _service is not None:
        _service.close()
        _service = None


def _translate(exc: RazorpayError) -> JSONResponse:
    """Map typed integration failures to safe HTTP error responses."""
    if isinstance(exc, RazorpayNotFoundError):
        status, message = 404, str(exc)
    elif isinstance(exc, RazorpayAuthenticationError):
        # Static text only: never echo credential-related diagnostics.
        status, message = 502, "Razorpay authentication failed"
    elif isinstance(exc, RazorpayValidationError):
        status, message = 502, f"Razorpay rejected the request: {exc}"
    elif isinstance(exc, RazorpayRateLimitError):
        status, message = 503, "Razorpay rate limit exceeded; retry later"
    elif isinstance(exc, RazorpayServerError):
        status, message = 502, "Razorpay service error"
    elif isinstance(exc, RazorpayConnectionError):
        status, message = 504, "Could not reach Razorpay"
    else:
        status, message = 502, "Unexpected Razorpay response"
    logger.warning(
        "Razorpay integration failure (%s): %s", type(exc).__name__, message
    )
    return JSONResponse(status_code=status, content={"detail": message})


# --- connection verification ----------------------------------------------

# (exception type, HTTP status, application error code, sanitized message)
_CONNECTION_FAILURE_MAP: list[tuple[type[RazorpayError], int, str, str]] = [
    (RazorpayAuthenticationError, 502, "authentication_failed",
     "Razorpay rejected the configured credentials"),
    (RazorpayRateLimitError, 503, "rate_limited",
     "Razorpay rate limit exceeded; retry later"),
    (RazorpayServerError, 502, "upstream_error",
     "Razorpay service error; retry later"),
    (RazorpayConnectionError, 504, "unreachable",
     "Could not reach Razorpay (network failure or timeout)"),
    (RazorpayInvalidResponseError, 502, "invalid_response",
     "Razorpay returned an unexpected response shape"),
    (RazorpayValidationError, 502, "upstream_rejected",
     "Razorpay rejected the verification request"),
]


def _connection_failure(
    *,
    status_code: int,
    code: str,
    message: str,
    environment: str,
) -> JSONResponse:
    """Build a structured, sanitized failure body for /connection."""
    body = ConnectionStatusResponse(
        connected=False,
        environment=environment,  # type: ignore[arg-type]
        error=ConnectionErrorDetail(code=code, message=message),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _map_connection_error(exc: RazorpayError, environment: str) -> JSONResponse:
    """Translate a verification failure into a structured HTTP response."""
    for exc_type, status_code, code, message in _CONNECTION_FAILURE_MAP:
        if isinstance(exc, exc_type):
            return _connection_failure(
                status_code=status_code,
                code=code,
                message=message,
                environment=environment,
            )
    return _connection_failure(
        status_code=502,
        code="verification_failed",
        message="Razorpay connection could not be verified",
        environment=environment,
    )


@router.get(
    "/connection",
    response_model=ConnectionStatusResponse,
    summary="Verify Razorpay credentials and connectivity",
    description=(
        "Performs a minimal authenticated request against the Razorpay API "
        "(`GET /payments?count=1`) using the configured credentials and "
        "reports whether they work. Responses contain only the credential "
        "mode (`test`/`live`/`unknown`) and sanitized error codes — never "
        "key ids, secrets, or provider payload details. Missing credentials "
        "yield 503 with `not_configured`; invalid credentials 502 with "
        "`authentication_failed`; timeouts/network failures 504 with "
        "`unreachable`; persistent rate limits 503 with `rate_limited`; "
        "upstream 5xx 502 with `upstream_error`."
    ),
    responses={
        200: {
            "model": ConnectionStatusResponse,
            "description": "Credentials accepted by Razorpay",
        },
        502: {
            "model": ConnectionStatusResponse,
            "description": "Invalid credentials, upstream rejection/failure "
            "(`authentication_failed`, `upstream_error`, `invalid_response`)",
        },
        503: {
            "model": ConnectionStatusResponse,
            "description": "Credentials not configured (`not_configured`) or "
            "rate limited (`rate_limited`)",
        },
        504: {
            "model": ConnectionStatusResponse,
            "description": "Razorpay unreachable (`unreachable`)",
        },
    },
)
def verify_razorpay_connection() -> ConnectionStatusResponse | JSONResponse:
    settings = get_settings()
    environment = detect_key_mode(settings.razorpay_key_id)

    if not settings.razorpay_configured:
        return _connection_failure(
            status_code=503,
            code="not_configured",
            message="Razorpay credentials are not configured",
            environment=environment,
        )

    global _service
    if _service is None:
        try:
            _service = RazorpayService.from_settings(settings)
        except RazorpayError as exc:
            return _map_connection_error(exc, environment)

    try:
        probe = _service.verify_connection()
    except RazorpayError as exc:
        return _map_connection_error(exc, environment)

    return ConnectionStatusResponse(
        connected=True,
        environment=probe.environment,  # type: ignore[arg-type]
    )


def _epoch_range_guard(from_ts: int | None, to_ts: int | None) -> None:
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(
            status_code=422, detail="'from' must be less than or equal to 'to'"
        )


@router.get(
    "/payments",
    response_model=PaymentListResponse,
    summary="List Razorpay payments (normalized)",
    description=(
        "Fetches one bounded page of payments directly from Razorpay and "
        "returns them normalized: exact minor-unit amounts, UTC creation "
        "timestamps, provider fee/tax when levied. Pagination mirrors the "
        "provider (`count` 1-100, `skip`, inclusive Unix-second `from`/`to`). "
        "Responses never include credentials or raw provider payloads; "
        "integration failures are translated to sanitized errors."
    ),
    responses={
        422: {"description": "Invalid query parameters (bounds, epoch range)"},
        502: {
            "description": "Razorpay rejected the request or returned an "
            "unexpected response (invalid credentials, upstream rejection/"
            "failure, malformed payload)"
        },
        503: {
            "description": "Credentials not configured, or Razorpay rate "
            "limit exhausted after retries"
        },
        504: {"description": "Razorpay unreachable (network failure/timeout)"},
    },
)
def list_payments(
    count: int = Query(
        DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Page size"
    ),
    skip: int = Query(0, ge=0, le=MAX_SKIP, description="Records to skip"),
    from_ts: int | None = Query(
        None,
        alias="from",
        ge=EPOCH_MIN,
        le=EPOCH_MAX,
        description="Unix seconds (inclusive) lower bound",
    ),
    to_ts: int | None = Query(
        None,
        alias="to",
        ge=EPOCH_MIN,
        le=EPOCH_MAX,
        description="Unix seconds (inclusive) upper bound",
    ),
    service: RazorpayService = Depends(get_razorpay_service),
) -> PaymentListResponse | JSONResponse:
    _epoch_range_guard(from_ts, to_ts)
    try:
        return service.list_payments(
            count=count, skip=skip, from_epoch=from_ts, to_epoch=to_ts
        )
    except RazorpayError as exc:
        return _translate(exc)


@router.get(
    "/settlements",
    response_model=SettlementListResponse,
    summary="List Razorpay settlements (normalized)",
    description=(
        "Fetches one bounded page of settlements directly from Razorpay and "
        "returns them normalized: exact minor-unit amount/fees/tax, UTC "
        "creation timestamps, and the bank UTR used to correlate payouts "
        "with payments. Pagination mirrors the provider (`count` 1-100, "
        "`skip`, inclusive Unix-second `from`/`to`). Responses never include "
        "credentials or raw provider payloads; integration failures are "
        "translated to sanitized errors."
    ),
    responses={
        422: {"description": "Invalid query parameters (bounds, epoch range)"},
        502: {
            "description": "Razorpay rejected the request or returned an "
            "unexpected response (invalid credentials, upstream rejection/"
            "failure, malformed payload)"
        },
        503: {
            "description": "Credentials not configured, or Razorpay rate "
            "limit exhausted after retries"
        },
        504: {"description": "Razorpay unreachable (network failure/timeout)"},
    },
)
def list_settlements(
    count: int = Query(
        DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Page size"
    ),
    skip: int = Query(0, ge=0, le=MAX_SKIP, description="Records to skip"),
    from_ts: int | None = Query(
        None,
        alias="from",
        ge=EPOCH_MIN,
        le=EPOCH_MAX,
        description="Unix seconds (inclusive) lower bound",
    ),
    to_ts: int | None = Query(
        None,
        alias="to",
        ge=EPOCH_MIN,
        le=EPOCH_MAX,
        description="Unix seconds (inclusive) upper bound",
    ),
    service: RazorpayService = Depends(get_razorpay_service),
) -> SettlementListResponse | JSONResponse:
    _epoch_range_guard(from_ts, to_ts)
    try:
        return service.list_settlements(
            count=count, skip=skip, from_epoch=from_ts, to_epoch=to_ts
        )
    except RazorpayError as exc:
        return _translate(exc)


__all__ = ["get_razorpay_service", "reset_razorpay_service", "router"]

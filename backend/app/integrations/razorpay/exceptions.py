"""Typed exceptions for the Razorpay integration.

Contract:

- Every exception carries a safe, human-readable message built only from
  HTTP status and Razorpay's own error fields (``code``, ``description``,
  ``field``, ...). Credentials, Authorization headers, and raw response
  bodies are never embedded.
- Structured detail is available programmatically via ``RazorpayError.detail``
  for logging/investigation without re-parsing payloads.
"""

from dataclasses import dataclass, field as _dc_field
from typing import Any


@dataclass(frozen=True)
class RazorpayErrorDetail:
    """Diagnostic subset of Razorpay's structured error payload.

    See https://razorpay.com/docs/api/errors/. All members are optional:
    Razorpay omits fields depending on resource and failure mode.
    """

    code: str | None = None
    description: str | None = None
    field: str | None = None
    source: str | None = None
    step: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = _dc_field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: object) -> "RazorpayErrorDetail":
        """Defensively parse ``{"error": {...}}`` from an arbitrary body.

        Never raises: unexpected shapes degrade to an empty detail so that
        error mapping always succeeds.
        """
        if not isinstance(payload, dict):
            return cls()
        error = payload.get("error")
        if not isinstance(error, dict):
            return cls()

        def _text(key: str) -> str | None:
            value = error.get(key)
            if value is None or isinstance(value, (dict, list)):
                return None
            return str(value)

        metadata = error.get("metadata")
        return cls(
            code=_text("code"),
            description=_text("description"),
            field=_text("field"),
            source=_text("source"),
            step=_text("step"),
            reason=_text("reason"),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def describe(self) -> str:
        """Safe one-line summary for exception messages (no secrets)."""
        parts: list[str] = []
        if self.code:
            parts.append(f"code={self.code}")
        if self.description:
            parts.append(self.description)
        if self.field:
            parts.append(f"field={self.field}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        return ", ".join(parts)


class RazorpayError(Exception):
    """Base class for all Razorpay integration failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: RazorpayErrorDetail | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class RazorpayAuthenticationError(RazorpayError):
    """HTTP 401/403 — invalid or unauthorized API credentials."""


class RazorpayValidationError(RazorpayError):
    """HTTP 400/422 — Razorpay rejected the request parameters."""


class RazorpayNotFoundError(RazorpayError):
    """HTTP 404 — the requested Razorpay resource does not exist."""


class RazorpayRateLimitError(RazorpayError):
    """HTTP 429 persisted after exhausting bounded retries."""


class RazorpayServerError(RazorpayError):
    """HTTP 5xx persisted after exhausting bounded retries."""


class RazorpayConnectionError(RazorpayError):
    """Network failure or timeout while reaching the Razorpay API."""


class RazorpayInvalidResponseError(RazorpayError):
    """Razorpay responded 2xx but the body was not the expected shape.

    Raised for malformed JSON and for payloads failing wire-model
    validation, so consumers can distinguish provider contract drift from
    transport/auth/rate-limit failures.
    """

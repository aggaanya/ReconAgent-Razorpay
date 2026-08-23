"""Razorpay connection-status API schemas (safe public surface).

Responses intentionally carry only the credential mode (``test``/``live``/
``unknown``) and coarse error codes — never key ids, secrets, Authorization
headers, or raw provider payloads.
"""

from typing import Literal

from pydantic import BaseModel, Field

KeyMode = Literal["test", "live", "unknown"]


class ConnectionErrorDetail(BaseModel):
    """Structured, sanitized application-level failure information."""

    code: str = Field(
        examples=["authentication_failed", "rate_limited", "unreachable"],
        description="Stable application error code for programmatic handling",
    )
    message: str = Field(
        description="Human-readable summary safe to display (no credentials)"
    )


class ConnectionStatusResponse(BaseModel):
    """Result of verifying Razorpay credentials and connectivity."""

    connected: bool = Field(description="Whether verification succeeded")
    environment: KeyMode = Field(
        description="Credential mode derived from the configured key id"
    )
    error: ConnectionErrorDetail | None = Field(
        default=None,
        description="Present only when connected is false",
    )

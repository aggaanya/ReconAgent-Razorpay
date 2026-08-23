"""Application configuration loaded from environment variables / .env.

Every integration credential is optional so the service can boot without
future dependencies (database, LLM, Razorpay). Secret values use
``SecretStr`` so they are never exposed through ``repr``/logs.

Razorpay settings (key id, secret, base URL, HTTP timeout, retry budget)
are validated at startup and consumed by the client in
``app.integrations.razorpay``.
"""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]

DEFAULT_CORS_ORIGINS = ["http://localhost:5173"]

# Razorpay public REST API. Overridable for testing; must always be HTTPS.
DEFAULT_RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"
DEFAULT_RAZORPAY_TIMEOUT_SECONDS = 10.0
# Retries apply only to transient failures (429/5xx statuses and network
# errors) on read requests; backoff is stepped with optional jitter.
DEFAULT_RAZORPAY_MAX_RETRIES = 3
RAZORPAY_MAX_RETRIES_UPPER_BOUND = 10
DEFAULT_RAZORPAY_RETRY_BASE_DELAY_SECONDS = 0.5
DEFAULT_RAZORPAY_RETRY_MAX_DELAY_SECONDS = 30.0


def _is_supported_database_url(url: str) -> bool:
    """Accept ``postgresql://``, ``postgres://`` and driver-qualified
    forms such as ``postgresql+psycopg2://``. Reject everything else."""
    scheme = url.split("://", 1)[0].lower()
    return scheme in {"postgresql", "postgres"} or scheme.startswith(
        "postgresql+"
    )


class Settings(BaseSettings):
    """Environment-driven settings for the ReconAgent backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "ReconAgent API"
    version: str = "0.1.0"
    environment: Environment = "development"

    cors_origins: Annotated[list[str], NoDecode] = list(DEFAULT_CORS_ORIGINS)

    # Integration credentials (all optional until their phase lands).
    database_url: str | None = None
    jwt_secret: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None

    # Razorpay integration settings (consumed by app.integrations.razorpay).
    razorpay_base_url: str = DEFAULT_RAZORPAY_BASE_URL
    razorpay_timeout_seconds: float = DEFAULT_RAZORPAY_TIMEOUT_SECONDS
    razorpay_max_retries: int = DEFAULT_RAZORPAY_MAX_RETRIES
    # Retry backoff shaping: delays grow from base by a fixed multiplier and
    # are capped at max; jitter (enabled by default) randomizes each delay
    # within [0, computed] to prevent synchronized thundering-herd retries.
    razorpay_retry_base_delay_seconds: float = (
        DEFAULT_RAZORPAY_RETRY_BASE_DELAY_SECONDS
    )
    razorpay_retry_max_delay_seconds: float = (
        DEFAULT_RAZORPAY_RETRY_MAX_DELAY_SECONDS
    )
    razorpay_retry_jitter: bool = True

    # Synchronization workflow pacing/bounds. The provider allows 100
    # records per page; pacing sleeps between pages so long backfills do
    # not hammer the API, and max_pages bounds a single run's duration.
    razorpay_sync_page_size: int = 100
    razorpay_sync_max_pages: int = 200
    razorpay_sync_page_delay_seconds: float = 0.25

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("razorpay_key_id", "razorpay_key_secret", mode="before")
    @classmethod
    def _blank_razorpay_credentials_are_unset(cls, value: object) -> object:
        """Treat ``RAZORPAY_KEY_ID=``/``RAZORPAY_KEY_SECRET=`` as unset so a
        half-filled .env cannot masquerade as configured."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("razorpay_base_url")
    @classmethod
    def _validate_razorpay_base_url(cls, value: str) -> str:
        """Require an absolute HTTPS URL; normalize away trailing slashes."""
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(
                "RAZORPAY_BASE_URL must be an absolute HTTPS URL "
                f"(got scheme {parsed.scheme or 'none'!r})"
            )
        return normalized

    @field_validator("razorpay_timeout_seconds")
    @classmethod
    def _validate_razorpay_timeout_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("RAZORPAY_TIMEOUT_SECONDS must be a positive number")
        return value

    @field_validator("razorpay_max_retries")
    @classmethod
    def _validate_razorpay_max_retries(cls, value: int) -> int:
        if not 0 <= value <= RAZORPAY_MAX_RETRIES_UPPER_BOUND:
            raise ValueError(
                "RAZORPAY_MAX_RETRIES must be between 0 and "
                f"{RAZORPAY_MAX_RETRIES_UPPER_BOUND} (got {value})"
            )
        return value

    @field_validator("razorpay_retry_base_delay_seconds")
    @classmethod
    def _validate_retry_base_delay(cls, value: float) -> float:
        if value < 0:
            raise ValueError(
                "RAZORPAY_RETRY_BASE_DELAY_SECONDS must be non-negative"
            )
        return value

    @field_validator("razorpay_retry_max_delay_seconds")
    @classmethod
    def _validate_retry_max_delay(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(
                "RAZORPAY_RETRY_MAX_DELAY_SECONDS must be a positive number"
            )
        return value

    @model_validator(mode="after")
    def _retry_delay_window_is_sane(self) -> "Settings":
        if (
            self.razorpay_retry_max_delay_seconds
            < self.razorpay_retry_base_delay_seconds
        ):
            raise ValueError(
                "RAZORPAY_RETRY_MAX_DELAY_SECONDS must be greater than or "
                "equal to RAZORPAY_RETRY_BASE_DELAY_SECONDS"
            )
        return self

    @field_validator("razorpay_sync_page_size")
    @classmethod
    def _validate_sync_page_size(cls, value: int) -> int:
        if not 1 <= value <= 100:
            raise ValueError("RAZORPAY_SYNC_PAGE_SIZE must be between 1 and 100")
        return value

    @field_validator("razorpay_sync_max_pages")
    @classmethod
    def _validate_sync_max_pages(cls, value: int) -> int:
        if value < 1:
            raise ValueError("RAZORPAY_SYNC_MAX_PAGES must be at least 1")
        return value

    @field_validator("razorpay_sync_page_delay_seconds")
    @classmethod
    def _validate_sync_delay(cls, value: float) -> float:
        if value < 0 or value > 60:
            raise ValueError(
                "RAZORPAY_SYNC_PAGE_DELAY_SECONDS must be between 0 and 60"
            )
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def razorpay_configured(self) -> bool:
        """Whether both Razorpay credentials are present (never their values)."""
        return self.razorpay_key_id is not None and self.razorpay_key_secret is not None

    @property
    def database_url_supported(self) -> bool:
        """Whether DATABASE_URL, when set, is a supported PostgreSQL URL."""
        return self.database_url is None or _is_supported_database_url(
            self.database_url
        )

    def configuration_issues(self) -> list[str]:
        """Return human-readable misconfiguration problems (empty when valid).

        Phase 1 validates only what the running process can reason about:
        PostgreSQL URL shape and secrets that must exist before production
        traffic. Connectivity checks arrive with the database layer in Phase 2.
        """
        issues: list[str] = []

        if self.database_url and not _is_supported_database_url(self.database_url):
            scheme = self.database_url.split("://", 1)[0].lower()
            issues.append(
                f"DATABASE_URL scheme '{scheme}://' is not supported; "
                "expected postgresql:// (optionally with a driver, e.g. "
                "postgresql+psycopg2://)"
            )

        if self.is_production:
            if self.jwt_secret is None:
                issues.append("JWT_SECRET must be set when ENVIRONMENT=production")
            if self.llm_api_key is None:
                issues.append("LLM_API_KEY must be set when ENVIRONMENT=production")

        return issues


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()

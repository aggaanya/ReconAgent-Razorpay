"""Application configuration loaded from environment variables / .env.

Every integration credential is optional so the service can boot without
external dependencies (database, LLM). Secret values use ``SecretStr`` so
they are never exposed through ``repr``/logs. ReconAgent operates on a
normalized internal financial data model; synthetic data is provided for
deterministic demos and evaluation — no external payment-provider
credentials exist in configuration.

LLM settings (model, optional base URL, timeout, retry budget) are
validated here and consumed by the centralized service in ``app.ai.llm``.
"""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]

DEFAULT_CORS_ORIGINS = ["http://localhost:5173"]

# LLM integration (consumed by app.ai.llm). The service targets the
# OpenAI-compatible chat-completions API; LLM_BASE_URL may repoint it at
# any compatible endpoint without code changes.
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
DEFAULT_LLM_MAX_RETRIES = 2
LLM_MAX_RETRIES_UPPER_BOUND = 10


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

    # LLM integration settings (consumed by app.ai.llm).
    llm_model: str = DEFAULT_LLM_MODEL
    llm_base_url: str | None = None
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    # Client-side retries for transient LLM failures (429/5xx/network);
    # the SDK applies them before surfacing an error to the service.
    llm_max_retries: int = DEFAULT_LLM_MAX_RETRIES

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def _blank_credentials_are_unset(cls, value: object) -> object:
        """Treat blank credential env values (``LLM_API_KEY=`` etc.) as
        unset so a half-filled .env cannot masquerade as configured."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("llm_model", mode="before")
    @classmethod
    def _blank_llm_model_uses_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return DEFAULT_LLM_MODEL
        return value

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def _validate_llm_base_url(cls, value: object) -> object:
        """Optional absolute HTTPS URL; normalize away trailing slashes."""
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        if not isinstance(value, str):
            return value
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(
                "LLM_BASE_URL must be an absolute HTTPS URL "
                f"(got scheme {parsed.scheme or 'none'!r})"
            )
        return normalized

    @field_validator("llm_timeout_seconds")
    @classmethod
    def _validate_llm_timeout_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be a positive number")
        return value

    @field_validator("llm_max_retries")
    @classmethod
    def _validate_llm_max_retries(cls, value: int) -> int:
        if not 0 <= value <= LLM_MAX_RETRIES_UPPER_BOUND:
            raise ValueError(
                "LLM_MAX_RETRIES must be between 0 and "
                f"{LLM_MAX_RETRIES_UPPER_BOUND} (got {value})"
            )
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def llm_configured(self) -> bool:
        """Whether an LLM API key is present (never its value)."""
        return self.llm_api_key is not None

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

"""Application configuration loaded from environment variables / .env.

Phase 1 scope: configuration readiness only. Every integration credential is
optional so the service can boot without future dependencies (database, LLM,
Razorpay). Secret values use ``SecretStr`` so they are never exposed through
``repr``/logs.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]

DEFAULT_CORS_ORIGINS = ["http://localhost:5173"]


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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

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

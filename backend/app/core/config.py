"""Application configuration loaded from environment variables / .env.

Every integration credential is optional so the service can boot without
external dependencies (database, LLM). Secret values use ``SecretStr`` so
they are never exposed through ``repr``/logs.

ReconAgent operates on a normalized internal financial data model;
synthetic data is provided for deterministic demos and evaluation — no
external payment-provider credentials exist in configuration.

LLM settings (model, optional base URL, timeout, retry budget) are
validated here and consumed by the centralized service in ``app.ai.llm``.

For local development, Ollama is supported through its local
OpenAI-compatible HTTP endpoint:

    http://127.0.0.1:11434/v1

Remote LLM endpoints must use HTTPS.
"""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Hugging Face Inference API defaults (OpenAI-compatible endpoint)
DEFAULT_HF_BASE_URL = "https://api-inference.huggingface.co/v1"
DEFAULT_HF_MODEL = "google/gemma-2-2b-it"

# Google Gemini API defaults (OpenAI-compatible endpoint)
DEFAULT_GEMINI_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai"
)
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"


Environment = Literal["development", "test", "production"]

DEFAULT_CORS_ORIGINS = ["http://localhost:5173"]

# LLM integration
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
DEFAULT_LLM_MAX_RETRIES = 2
LLM_MAX_RETRIES_UPPER_BOUND = 10


def _is_supported_database_url(url: str) -> bool:
    """Accept PostgreSQL connection URLs."""
    scheme = url.split("://", 1)[0].lower()

    return (
        scheme in {"postgresql", "postgres"}
        or scheme.startswith("postgresql+")
    )


def _is_local_ollama_url(parsed_url) -> bool:
    """Return True when the URL points to a local Ollama server.

    Ollama normally exposes its OpenAI-compatible API over HTTP at:

        http://127.0.0.1:11434/v1

    HTTP is intentionally allowed only for localhost/127.0.0.1.
    """

    return (
        parsed_url.scheme == "http"
        and parsed_url.hostname in {"127.0.0.1", "localhost"}
    )


class Settings(BaseSettings):
    """Environment-driven settings for the ReconAgent backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    app_name: str = "ReconAgent API"
    version: str = "0.1.0"
    environment: Environment = "development"

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    cors_origins: Annotated[list[str], NoDecode] = list(
        DEFAULT_CORS_ORIGINS
    )

    # ------------------------------------------------------------------
    # Integration credentials
    # ------------------------------------------------------------------

    database_url: str | None = None

    jwt_secret: SecretStr | None = None

    llm_api_key: SecretStr | None = None

    # ------------------------------------------------------------------
    # LLM configuration
    # ------------------------------------------------------------------

    llm_model: str = DEFAULT_LLM_MODEL

    llm_base_url: str | None = None

    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS

    llm_max_retries: int = DEFAULT_LLM_MAX_RETRIES

    # ------------------------------------------------------------------
    # Hugging Face Inference API (convenience aliases)
    # ------------------------------------------------------------------
    # When set, these are mapped to the LLM_* fields above so the rest
    # of the application needs no changes.  LLM_* always wins when both
    # are specified, keeping Ollama / custom providers fully supported.

    hf_token: SecretStr | None = None

    hf_model: str | None = None

    hf_base_url: str | None = None

    # ------------------------------------------------------------------
    # DeepSeek API (convenience aliases)
    # ------------------------------------------------------------------
    # When set, these are mapped to the LLM_* fields above.  DeepSeek
    # takes priority over HF when both are specified.

    deepseek_api_key: SecretStr | None = None

    deepseek_base_url: str | None = None

    deepseek_model: str | None = None

    # ------------------------------------------------------------------
    # Google Gemini API (convenience aliases)
    # ------------------------------------------------------------------
    # When set, these are mapped to the LLM_* fields above.  Gemini
    # takes priority over HF when both are specified.

    gemini_api_key: SecretStr | None = None

    gemini_model: str | None = None

    gemini_base_url: str | None = None

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        """Convert comma-separated CORS origins into a list."""

        if isinstance(value, str):
            return [
                origin.strip()
                for origin in value.split(",")
                if origin.strip()
            ]

        return value

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def _blank_credentials_are_unset(
        cls,
        value: object,
    ) -> object:
        """Treat blank LLM credentials as unset."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _blank_database_url_is_unset(cls, value: object) -> object:
        """Treat an empty DATABASE_URL as an unconfigured database."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("llm_model", mode="before")
    @classmethod
    def _blank_llm_model_uses_default(
        cls,
        value: object,
    ) -> object:
        """Use the default model when LLM_MODEL is blank."""

        if isinstance(value, str) and not value.strip():
            return DEFAULT_LLM_MODEL

        return value

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def _validate_llm_base_url(
        cls,
        value: object,
    ) -> object:
        """Validate the LLM endpoint URL.

        Rules:

        1. Empty values are treated as unset.
        2. Remote endpoints MUST use HTTPS.
        3. Local Ollama endpoints may use HTTP.
        4. HTTP is allowed only for localhost / 127.0.0.1.
        5. Trailing slashes are removed.
        """

        if value is None:
            return None

        if isinstance(value, str) and not value.strip():
            return None

        if not isinstance(value, str):
            return value

        normalized = value.strip().rstrip("/")

        parsed = urlparse(normalized)

        # URL must have a scheme and network location.
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                "LLM_BASE_URL must be an absolute URL"
            )

        # HTTPS is required for remote providers.
        #
        # HTTP is permitted only for local Ollama.
        if parsed.scheme == "https":
            return normalized

        if _is_local_ollama_url(parsed):
            return normalized

        raise ValueError(
            "LLM_BASE_URL must use HTTPS for remote endpoints. "
            "HTTP is allowed only for local Ollama at "
            "localhost or 127.0.0.1."
        )

    @field_validator("llm_timeout_seconds")
    @classmethod
    def _validate_llm_timeout_seconds(
        cls,
        value: float,
    ) -> float:
        """Ensure the LLM timeout is positive."""

        if value <= 0:
            raise ValueError(
                "LLM_TIMEOUT_SECONDS must be a positive number"
            )

        return value

    @field_validator("llm_max_retries")
    @classmethod
    def _validate_llm_max_retries(
        cls,
        value: int,
    ) -> int:
        """Validate the LLM retry budget."""

        if not 0 <= value <= LLM_MAX_RETRIES_UPPER_BOUND:
            raise ValueError(
                "LLM_MAX_RETRIES must be between 0 and "
                f"{LLM_MAX_RETRIES_UPPER_BOUND} "
                f"(got {value})"
            )

        return value

    @field_validator("hf_token", mode="before")
    @classmethod
    def _blank_hf_token_is_unset(
        cls,
        value: object,
    ) -> object:
        """Treat blank Hugging Face tokens as unset."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("hf_model", mode="before")
    @classmethod
    def _blank_hf_model_is_unset(
        cls,
        value: object,
    ) -> object:
        """Treat blank Hugging Face model names as unset."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("hf_base_url", mode="before")
    @classmethod
    def _validate_hf_base_url(
        cls,
        value: object,
    ) -> object:
        """Validate the Hugging Face Inference API endpoint URL.

        Rules:

        1. Empty values are treated as unset.
        2. HTTPS is required (remote endpoint).
        3. Trailing slashes are removed.
        """

        if value is None:
            return None

        if isinstance(value, str) and not value.strip():
            return None

        if not isinstance(value, str):
            return value

        normalized = value.strip().rstrip("/")

        parsed = urlparse(normalized)

        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                "HF_BASE_URL must be an absolute URL"
            )

        if parsed.scheme != "https":
            raise ValueError(
                "HF_BASE_URL must use HTTPS"
            )

        return normalized

    @field_validator("deepseek_api_key", mode="before")
    @classmethod
    def _blank_deepseek_api_key_is_unset(
        cls,
        value: object,
    ) -> object:
        """Treat blank DeepSeek API keys as unset."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("deepseek_model", mode="before")
    @classmethod
    def _blank_deepseek_model_is_unset(
        cls,
        value: object,
    ) -> object:
        """Treat blank DeepSeek model names as unset."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("deepseek_base_url", mode="before")
    @classmethod
    def _validate_deepseek_base_url(
        cls,
        value: object,
    ) -> object:
        """Validate the DeepSeek API endpoint URL.

        Rules:

        1. Empty values are treated as unset.
        2. HTTPS is required.
        3. Trailing slashes are removed.
        """

        if value is None:
            return None

        if isinstance(value, str) and not value.strip():
            return None

        if not isinstance(value, str):
            return value

        normalized = value.strip().rstrip("/")

        parsed = urlparse(normalized)

        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                "DEEPSEEK_BASE_URL must be an absolute URL"
            )

        if parsed.scheme != "https":
            raise ValueError(
                "DEEPSEEK_BASE_URL must use HTTPS"
            )

        return normalized

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _blank_gemini_api_key_is_unset(
        cls,
        value: object,
    ) -> object:
        """Treat blank Gemini API keys as unset."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("gemini_model", mode="before")
    @classmethod
    def _blank_gemini_model_is_unset(
        cls,
        value: object,
    ) -> object:
        """Treat blank Gemini model names as unset."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator("gemini_base_url", mode="before")
    @classmethod
    def _validate_gemini_base_url(
        cls,
        value: object,
    ) -> object:
        """Validate the Google Gemini API endpoint URL.

        Rules:

        1. Empty values are treated as unset.
        2. HTTPS is required (remote endpoint).
        3. Trailing slashes are removed.
        """

        if value is None:
            return None

        if isinstance(value, str) and not value.strip():
            return None

        if not isinstance(value, str):
            return value

        normalized = value.strip().rstrip("/")

        parsed = urlparse(normalized)

        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                "GEMINI_BASE_URL must be an absolute URL"
            )

        if parsed.scheme != "https":
            raise ValueError(
                "GEMINI_BASE_URL must use HTTPS"
            )

        return normalized

    @model_validator(mode="after")
    def _resolve_providers_to_llm(self) -> "Settings":
        """Map provider-specific env vars to the LLM fields.

        Priority: LLM_* > Gemini > DeepSeek > HF > defaults.
        LLM_* always wins when both are explicitly set.
        """
        # Gemini takes priority over other aliases
        if self.gemini_api_key is not None and self.llm_api_key is None:
            object.__setattr__(self, "llm_api_key", self.gemini_api_key)

        if self.gemini_model is not None and self.llm_model == DEFAULT_LLM_MODEL:
            object.__setattr__(self, "llm_model", self.gemini_model)

        if self.gemini_base_url is not None and self.llm_base_url is None:
            object.__setattr__(self, "llm_base_url", self.gemini_base_url)

        # DeepSeek — second priority after Gemini
        if self.deepseek_api_key is not None and self.llm_api_key is None:
            object.__setattr__(self, "llm_api_key", self.deepseek_api_key)

        if self.deepseek_model is not None and self.llm_model == DEFAULT_LLM_MODEL:
            object.__setattr__(self, "llm_model", self.deepseek_model)

        if self.deepseek_base_url is not None and self.llm_base_url is None:
            object.__setattr__(self, "llm_base_url", self.deepseek_base_url)

        # HF fallback when Gemini/DeepSeek are not configured
        if self.hf_token is not None and self.llm_api_key is None:
            object.__setattr__(self, "llm_api_key", self.hf_token)

        if self.hf_model is not None and self.llm_model == DEFAULT_LLM_MODEL:
            object.__setattr__(self, "llm_model", self.hf_model)

        if self.hf_base_url is not None and self.llm_base_url is None:
            object.__setattr__(self, "llm_base_url", self.hf_base_url)

        return self

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        """Return whether the application is running in production."""

        return self.environment == "production"

    @property
    def llm_configured(self) -> bool:
        """Return whether an LLM API key is configured."""

        return self.llm_api_key is not None

    @property
    def database_url_supported(self) -> bool:
        """Return whether DATABASE_URL is a supported PostgreSQL URL."""

        return (
            self.database_url is None
            or _is_supported_database_url(self.database_url)
        )

    # ------------------------------------------------------------------
    # Configuration diagnostics
    # ------------------------------------------------------------------

    def configuration_issues(self) -> list[str]:
        """Return human-readable configuration problems."""

        issues: list[str] = []

        if (
            self.database_url
            and not _is_supported_database_url(self.database_url)
        ):
            scheme = self.database_url.split(
                "://",
                1,
            )[0].lower()

            issues.append(
                f"DATABASE_URL scheme '{scheme}://' is not supported; "
                "expected postgresql:// (optionally with a driver, "
                "e.g. postgresql+psycopg2://)"
            )

        if self.is_production:
            if self.jwt_secret is None:
                issues.append(
                    "JWT_SECRET must be set when "
                    "ENVIRONMENT=production"
                )

            if self.llm_api_key is None:
                issues.append(
                    "LLM_API_KEY must be set when "
                    "ENVIRONMENT=production"
                )

        return issues


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""

    return Settings()

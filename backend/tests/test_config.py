"""Pydantic Settings configuration behaviour."""

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import (
    DEFAULT_GEMINI_BASE_URL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_MAX_RETRIES,
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    LLM_MAX_RETRIES_UPPER_BOUND,
    LLM_MAX_TOKENS_UPPER_BOUND,
    Settings,
)

CREDENTIAL_ENV_VARS = (
    "DATABASE_URL",
    "JWT_SECRET",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "LLM_MAX_TOKENS",
    "ENVIRONMENT",
    "CORS_ORIGINS",
    "HF_TOKEN",
    "HF_MODEL",
    "HF_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_BASE_URL",
    "GEMINI_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_settings() -> Settings:
    # `_env_file=None` isolates tests from any developer `.env` file.
    return Settings(_env_file=None)


class TestDefaults:
    def test_credentials_are_optional(self, clean_env) -> None:
        settings = make_settings()
        assert settings.database_url is None
        assert settings.jwt_secret is None
        assert settings.llm_api_key is None

    def test_environment_defaults_to_development(self, clean_env) -> None:
        assert make_settings().environment == "development"

    def test_cors_defaults_to_frontend_origin(self, clean_env) -> None:
        assert make_settings().cors_origins == ["http://localhost:5173"]

    def test_app_metadata(self, clean_env) -> None:
        settings = make_settings()
        assert settings.app_name == "ReconAgent API"
        assert settings.version


class TestEnvOverrides:
    def test_database_url_override(self, clean_env) -> None:
        clean_env.setenv(
            "DATABASE_URL", "postgresql://user:pw@localhost:5432/reconagent"
        )
        assert make_settings().database_url.endswith("/reconagent")

    @pytest.mark.parametrize(
        "raw",
        [
            "http://localhost:5173,http://localhost:3000",
            " http://localhost:5173 , http://localhost:3000 ",
        ],
    )
    def test_cors_origins_comma_separated(self, clean_env, raw) -> None:
        clean_env.setenv("CORS_ORIGINS", raw)
        assert make_settings().cors_origins == [
            "http://localhost:5173",
            "http://localhost:3000",
        ]

    def test_invalid_environment_rejected(self, clean_env) -> None:
        clean_env.setenv("ENVIRONMENT", "staging")
        with pytest.raises(ValidationError):
            make_settings()

    def test_extra_env_vars_ignored(self, clean_env) -> None:
        clean_env.setenv("SOME_UNRELATED_VAR", "1")
        make_settings()  # must not raise


class TestSecretHandling:
    def test_secrets_are_masked_in_repr(self, clean_env) -> None:
        clean_env.setenv("JWT_SECRET", "leak-me-if-you-can")
        settings = make_settings()
        assert isinstance(settings.jwt_secret, SecretStr)
        assert "leak-me-if-you-can" not in repr(settings)
        assert "leak-me-if-you-can" not in str(settings.jwt_secret)

    def test_secret_value_accessible_internally(self, clean_env) -> None:
        clean_env.setenv("LLM_API_KEY", "sk-test-123")
        assert make_settings().llm_api_key.get_secret_value() == "sk-test-123"


class TestLLMConfiguration:
    """LLM_* settings: defaults, overrides, validation, configured state."""

    def test_defaults(self, clean_env) -> None:
        settings = make_settings()
        assert settings.llm_api_key is None
        assert settings.llm_model == DEFAULT_LLM_MODEL == "gpt-4o-mini"
        assert settings.llm_base_url is None
        assert settings.llm_timeout_seconds == DEFAULT_LLM_TIMEOUT_SECONDS
        assert settings.llm_max_retries == DEFAULT_LLM_MAX_RETRIES
        assert settings.llm_configured is False

    def test_key_and_model_overrides(self, clean_env) -> None:
        clean_env.setenv("LLM_API_KEY", "sk-test-123")
        clean_env.setenv("LLM_MODEL", "gpt-custom")
        settings = make_settings()
        assert settings.llm_api_key.get_secret_value() == "sk-test-123"
        assert settings.llm_model == "gpt-custom"
        assert settings.llm_configured is True

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_api_key_is_unconfigured(self, clean_env, raw) -> None:
        # A half-filled .env must not masquerade as configured.
        clean_env.setenv("LLM_API_KEY", raw)
        settings = make_settings()
        assert settings.llm_api_key is None
        assert settings.llm_configured is False

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_model_falls_back_to_default(self, clean_env, raw) -> None:
        clean_env.setenv("LLM_MODEL", raw)
        assert make_settings().llm_model == DEFAULT_LLM_MODEL

    def test_base_url_override_normalizes_trailing_slash(self, clean_env) -> None:
        clean_env.setenv("LLM_BASE_URL", "https://llm.example.com/v1/")
        assert make_settings().llm_base_url == "https://llm.example.com/v1"

    @pytest.mark.parametrize(
        "raw",
        [
            "http://llm.example.com/v1",  # insecure scheme
            "ftp://llm.example.com/v1",
            "llm.example.com/v1",  # no scheme
        ],
    )
    def test_insecure_or_invalid_base_url_rejected(self, clean_env, raw) -> None:
        clean_env.setenv("LLM_BASE_URL", raw)
        with pytest.raises(ValidationError):
            make_settings()

    def test_blank_base_url_is_unset_not_invalid(self, clean_env) -> None:
        clean_env.setenv("LLM_BASE_URL", "")
        assert make_settings().llm_base_url is None

    def test_timeout_override(self, clean_env) -> None:
        clean_env.setenv("LLM_TIMEOUT_SECONDS", "12.5")
        assert make_settings().llm_timeout_seconds == pytest.approx(12.5)

    @pytest.mark.parametrize("raw", ["0", "-1", "not-a-number", ""])
    def test_non_positive_or_invalid_timeout_rejected(
        self, clean_env, raw
    ) -> None:
        clean_env.setenv("LLM_TIMEOUT_SECONDS", raw)
        with pytest.raises(ValidationError):
            make_settings()

    def test_max_retries_override(self, clean_env) -> None:
        clean_env.setenv("LLM_MAX_RETRIES", "5")
        assert make_settings().llm_max_retries == 5

    def test_zero_max_retries_disables_retries(self, clean_env) -> None:
        clean_env.setenv("LLM_MAX_RETRIES", "0")
        assert make_settings().llm_max_retries == 0

    @pytest.mark.parametrize("raw", ["-1", "11", "not-a-number"])
    def test_invalid_max_retries_rejected(self, clean_env, raw) -> None:
        clean_env.setenv("LLM_MAX_RETRIES", raw)
        with pytest.raises(ValidationError):
            make_settings()

    def test_max_retries_upper_bound_constant_matches_validator(
        self,
    ) -> None:
        assert LLM_MAX_RETRIES_UPPER_BOUND == 10

    def test_max_tokens_default(self, clean_env) -> None:
        settings = make_settings()
        assert settings.llm_max_tokens == DEFAULT_LLM_MAX_TOKENS == 1024

    def test_max_tokens_override(self, clean_env) -> None:
        clean_env.setenv("LLM_MAX_TOKENS", "2048")
        assert make_settings().llm_max_tokens == 2048

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_max_tokens_is_unset(self, clean_env, raw) -> None:
        # A blank LLM_MAX_TOKENS means "provider default, no explicit cap".
        clean_env.setenv("LLM_MAX_TOKENS", raw)
        assert make_settings().llm_max_tokens is None

    @pytest.mark.parametrize(
        "raw",
        [
            "0",
            "-1",
            "8193",  # above the retry-doubling ceiling
            "not-a-number",
            "1.5",
        ],
    )
    def test_invalid_max_tokens_rejected(self, clean_env, raw) -> None:
        clean_env.setenv("LLM_MAX_TOKENS", raw)
        with pytest.raises(ValidationError):
            make_settings()

    def test_max_tokens_ceiling_matches_retry_budget(self) -> None:
        # The validated ceiling is exactly the retry double-cap used by
        # the LLM service when it re-issues a truncated call.
        assert LLM_MAX_TOKENS_UPPER_BOUND == 8192

    def test_key_masked_in_repr(self, clean_env) -> None:
        secret = "super-secret-llm-key"
        clean_env.setenv("LLM_API_KEY", secret)
        settings = make_settings()
        assert isinstance(settings.llm_api_key, SecretStr)
        assert secret not in repr(settings)

    def test_key_absent_from_validation_errors(self, clean_env) -> None:
        # An unrelated invalid setting must never echo the LLM key.
        clean_env.setenv("LLM_API_KEY", "super-secret-llm-key")
        clean_env.setenv("LLM_TIMEOUT_SECONDS", "-5")
        with pytest.raises(ValidationError) as exc_info:
            make_settings()
        assert "super-secret-llm-key" not in str(exc_info.value)

    def test_production_requires_api_key(self, clean_env) -> None:
        clean_env.setenv("ENVIRONMENT", "production")
        issues = make_settings().configuration_issues()
        assert any("LLM_API_KEY" in issue for issue in issues)


class TestConfigurationIssues:
    def test_no_issues_for_clean_development_config(self, clean_env) -> None:
        assert make_settings().configuration_issues() == []

    def test_flags_non_postgres_database_url(self, clean_env) -> None:
        clean_env.setenv("DATABASE_URL", "sqlite:///./dev.db")
        issues = make_settings().configuration_issues()
        assert issues and "DATABASE_URL" in issues[0]

    def test_accepts_postgres_schemes(self, clean_env) -> None:
        for scheme in ("postgresql", "postgres"):
            clean_env.setenv("DATABASE_URL", f"{scheme}://u:p@localhost/db")
            assert make_settings().configuration_issues() == []

    def test_accepts_driver_qualified_postgres_url(self, clean_env) -> None:
        clean_env.setenv(
            "DATABASE_URL",
            "postgresql+psycopg2://u:p@localhost:5432/reconagent",
        )
        settings = make_settings()
        assert settings.configuration_issues() == []
        assert settings.database_url_supported is True

    def test_database_url_supported_flags_bad_scheme(self, clean_env) -> None:
        clean_env.setenv("DATABASE_URL", "mysql://u:p@localhost/db")
        assert make_settings().database_url_supported is False

    def test_database_url_supported_when_unset(self, clean_env) -> None:
        assert make_settings().database_url_supported is True


class TestCachedSettings:
    def test_get_settings_is_cached(self, monkeypatch) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        assert get_settings() is get_settings()


class TestGeminiProvider:
    """Google Gemini provider alias: GEMINI_API_KEY / GEMINI_MODEL / GEMINI_BASE_URL."""

    def test_gemini_defaults(self, clean_env) -> None:
        settings = make_settings()
        assert settings.gemini_api_key is None
        assert settings.gemini_model is None
        assert settings.gemini_base_url is None

    def test_gemini_key_populates_llm_api_key(self, clean_env) -> None:
        clean_env.setenv("GEMINI_API_KEY", "gemini-test-key")
        settings = make_settings()
        assert settings.gemini_api_key is not None
        assert settings.gemini_api_key.get_secret_value() == "gemini-test-key"
        assert settings.llm_api_key is not None
        assert settings.llm_api_key.get_secret_value() == "gemini-test-key"

    def test_gemini_model_populates_llm_model(self, clean_env) -> None:
        clean_env.setenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
        settings = make_settings()
        assert settings.gemini_model == "gemini-3.1-pro-preview"
        assert settings.llm_model == "gemini-3.1-pro-preview"

    def test_gemini_base_url_populates_llm_base_url(self, clean_env) -> None:
        clean_env.setenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        )
        settings = make_settings()
        assert settings.llm_base_url == (
            "https://generativelanguage.googleapis.com/v1beta/openai"
        )

    def test_gemini_full_config(self, clean_env) -> None:
        clean_env.setenv("GEMINI_API_KEY", "gemini-key-123")
        clean_env.setenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
        clean_env.setenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        settings = make_settings()
        assert settings.llm_api_key.get_secret_value() == "gemini-key-123"
        assert settings.llm_model == "gemini-3.1-pro-preview"
        assert settings.llm_base_url == (
            "https://generativelanguage.googleapis.com/v1beta/openai"
        )
        assert settings.llm_configured is True

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_gemini_key_is_unconfigured(self, clean_env, raw) -> None:
        clean_env.setenv("GEMINI_API_KEY", raw)
        settings = make_settings()
        assert settings.gemini_api_key is None
        assert settings.llm_api_key is None

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_gemini_model_falls_back_to_default(
        self, clean_env, raw
    ) -> None:
        clean_env.setenv("GEMINI_MODEL", raw)
        assert make_settings().llm_model == DEFAULT_LLM_MODEL

    def test_blank_gemini_base_url_is_unset(self, clean_env) -> None:
        clean_env.setenv("GEMINI_BASE_URL", "")
        assert make_settings().llm_base_url is None

    def test_gemini_base_url_normalizes_trailing_slash(
        self, clean_env
    ) -> None:
        clean_env.setenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        assert make_settings().llm_base_url == (
            "https://generativelanguage.googleapis.com/v1beta/openai"
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "http://generativelanguage.googleapis.com/v1",  # insecure
            "ftp://generativelanguage.googleapis.com/v1",
            "generativelanguage.googleapis.com/v1",  # no scheme
        ],
    )
    def test_insecure_gemini_base_url_rejected(self, clean_env, raw) -> None:
        clean_env.setenv("GEMINI_BASE_URL", raw)
        with pytest.raises(ValidationError):
            make_settings()

    def test_gemini_key_is_masked_in_repr(self, clean_env) -> None:
        secret = "gemini-secret-key"
        clean_env.setenv("GEMINI_API_KEY", secret)
        settings = make_settings()
        assert isinstance(settings.gemini_api_key, SecretStr)
        assert secret not in repr(settings)
        assert secret not in str(settings.gemini_api_key)

    def test_llm_api_key_takes_priority_over_gemini(
        self, clean_env
    ) -> None:
        """LLM_API_KEY always wins when explicitly set."""
        clean_env.setenv("LLM_API_KEY", "llm-direct-key")
        clean_env.setenv("GEMINI_API_KEY", "gemini-key")
        settings = make_settings()
        assert settings.llm_api_key.get_secret_value() == "llm-direct-key"

    def test_gemini_takes_priority_over_deepseek(
        self, clean_env
    ) -> None:
        """Gemini aliases win over DeepSeek aliases when both are set."""
        clean_env.setenv("DEEPSEEK_API_KEY", "deepseek-key")
        clean_env.setenv("GEMINI_API_KEY", "gemini-key")
        settings = make_settings()
        assert settings.llm_api_key.get_secret_value() == "gemini-key"

    def test_gemini_takes_priority_over_hf(self, clean_env) -> None:
        """Gemini aliases win over HF aliases when both are set."""
        clean_env.setenv("GEMINI_API_KEY", "gemini-key")
        clean_env.setenv("HF_TOKEN", "hf-token")
        settings = make_settings()
        assert settings.llm_api_key.get_secret_value() == "gemini-key"

    def test_gemini_api_key_absent_from_validation_errors(
        self, clean_env
    ) -> None:
        clean_env.setenv("GEMINI_API_KEY", "gemini-secret-key")
        clean_env.setenv("LLM_TIMEOUT_SECONDS", "-5")
        with pytest.raises(ValidationError) as exc_info:
            make_settings()
        assert "gemini-secret-key" not in str(exc_info.value)

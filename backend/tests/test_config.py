"""Pydantic Settings configuration behaviour."""

import pytest
from pydantic import SecretStr

from app.core.config import Settings

CREDENTIAL_ENV_VARS = (
    "DATABASE_URL",
    "JWT_SECRET",
    "LLM_API_KEY",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "ENVIRONMENT",
    "CORS_ORIGINS",
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
    def test_future_credentials_are_optional(self, clean_env) -> None:
        settings = make_settings()
        assert settings.database_url is None
        assert settings.jwt_secret is None
        assert settings.llm_api_key is None
        assert settings.razorpay_key_id is None
        assert settings.razorpay_key_secret is None

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
        from pydantic import ValidationError

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


class TestCachedSettings:
    def test_get_settings_is_cached(self, monkeypatch) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        assert get_settings() is get_settings()

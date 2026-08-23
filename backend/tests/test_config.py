"""Pydantic Settings configuration behaviour."""

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import (
    DEFAULT_RAZORPAY_BASE_URL,
    DEFAULT_RAZORPAY_MAX_RETRIES,
    DEFAULT_RAZORPAY_TIMEOUT_SECONDS,
    RAZORPAY_MAX_RETRIES_UPPER_BOUND,
    Settings,
)

CREDENTIAL_ENV_VARS = (
    "DATABASE_URL",
    "JWT_SECRET",
    "LLM_API_KEY",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_BASE_URL",
    "RAZORPAY_TIMEOUT_SECONDS",
    "RAZORPAY_MAX_RETRIES",
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


class TestRazorpayConfiguration:
    """RAZORPAY_* settings: defaults, overrides, validation, configured state."""

    def test_default_base_url_and_timeout(self, clean_env) -> None:
        settings = make_settings()
        assert settings.razorpay_base_url == DEFAULT_RAZORPAY_BASE_URL
        assert settings.razorpay_base_url == "https://api.razorpay.com/v1"
        assert settings.razorpay_timeout_seconds == (
            DEFAULT_RAZORPAY_TIMEOUT_SECONDS
        )

    def test_default_constants_are_secure(self) -> None:
        # Guard rail: the shipped default must never regress to plain HTTP.
        assert DEFAULT_RAZORPAY_BASE_URL.startswith("https://")
        assert DEFAULT_RAZORPAY_TIMEOUT_SECONDS > 0

    def test_valid_configuration_loads(self, clean_env) -> None:
        clean_env.setenv("RAZORPAY_KEY_ID", "rzp_test_1A2b3C4d5E6f7G")
        clean_env.setenv("RAZORPAY_KEY_SECRET", "test-secret-value")
        settings = make_settings()
        assert settings.razorpay_key_id == "rzp_test_1A2b3C4d5E6f7G"
        assert settings.razorpay_key_secret.get_secret_value() == "test-secret-value"
        assert settings.razorpay_configured is True

    def test_missing_credentials_are_unconfigured(self, clean_env) -> None:
        settings = make_settings()
        assert settings.razorpay_key_id is None
        assert settings.razorpay_key_secret is None
        assert settings.razorpay_configured is False

    @pytest.mark.parametrize(
        ("key_id", "key_secret"),
        [
            ("rzp_test_abc", None),
            (None, "some-secret"),
            ("", ""),
            ("   ", "some-secret"),
        ],
    )
    def test_blank_or_partial_credentials_are_unconfigured(
        self, clean_env, key_id, key_secret
    ) -> None:
        if key_id is not None:
            clean_env.setenv("RAZORPAY_KEY_ID", key_id)
        if key_secret is not None:
            clean_env.setenv("RAZORPAY_KEY_SECRET", key_secret)
        settings = make_settings()
        assert settings.razorpay_configured is False

    def test_base_url_override_normalizes_trailing_slash(self, clean_env) -> None:
        clean_env.setenv("RAZORPAY_BASE_URL", "https://api.example.com/v1/")
        assert make_settings().razorpay_base_url == "https://api.example.com/v1"

    def test_timeout_override(self, clean_env) -> None:
        clean_env.setenv("RAZORPAY_TIMEOUT_SECONDS", "2.5")
        assert make_settings().razorpay_timeout_seconds == pytest.approx(2.5)

    @pytest.mark.parametrize(
        "raw",
        [
            "http://api.razorpay.com/v1",  # insecure scheme
            "ftp://api.razorpay.com/v1",
            "api.razorpay.com/v1",  # no scheme
            "",  # empty
        ],
    )
    def test_insecure_or_invalid_base_url_rejected(self, clean_env, raw) -> None:
        clean_env.setenv("RAZORPAY_BASE_URL", raw)
        with pytest.raises(ValidationError):
            make_settings()

    @pytest.mark.parametrize("raw", ["0", "-1", "not-a-number", ""])
    def test_non_positive_or_invalid_timeout_rejected(self, clean_env, raw) -> None:
        clean_env.setenv("RAZORPAY_TIMEOUT_SECONDS", raw)
        with pytest.raises(ValidationError):
            make_settings()

    def test_default_max_retries_is_bounded_and_sane(self, clean_env) -> None:
        settings = make_settings()
        assert settings.razorpay_max_retries == DEFAULT_RAZORPAY_MAX_RETRIES
        assert 0 <= DEFAULT_RAZORPAY_MAX_RETRIES <= RAZORPAY_MAX_RETRIES_UPPER_BOUND

    def test_max_retries_override(self, clean_env) -> None:
        clean_env.setenv("RAZORPAY_MAX_RETRIES", "7")
        assert make_settings().razorpay_max_retries == 7

    def test_zero_max_retries_disables_retries(self, clean_env) -> None:
        clean_env.setenv("RAZORPAY_MAX_RETRIES", "0")
        assert make_settings().razorpay_max_retries == 0

    @pytest.mark.parametrize("raw", ["-1", "11", "not-a-number"])
    def test_invalid_max_retries_rejected(self, clean_env, raw) -> None:
        clean_env.setenv("RAZORPAY_MAX_RETRIES", raw)
        with pytest.raises(ValidationError):
            make_settings()


class TestRazorpaySecretSafety:
    """The key secret must never surface in repr/logs/error text."""

    SECRET = "super-secret-razorpay-key"

    def _make_settings_with_secret(self, monkeypatch) -> Settings:
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_publicid")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", self.SECRET)
        return Settings(_env_file=None)

    def test_key_secret_masked_in_repr_and_str(self, clean_env) -> None:
        settings = self._make_settings_with_secret(clean_env)
        assert isinstance(settings.razorpay_key_secret, SecretStr)
        assert self.SECRET not in repr(settings)
        assert self.SECRET not in str(settings.razorpay_key_secret)

    def test_key_secret_absent_from_validation_errors(self, clean_env) -> None:
        # An unrelated invalid setting must never echo the Razorpay secret.
        clean_env.setenv("RAZORPAY_KEY_SECRET", self.SECRET)
        clean_env.setenv("RAZORPAY_TIMEOUT_SECONDS", "-5")
        with pytest.raises(ValidationError) as exc_info:
            make_settings()
        assert self.SECRET not in str(exc_info.value)

    def test_key_id_is_not_treated_as_a_secret(self, clean_env) -> None:
        # Key id is a public identifier (Basic Auth username); readable is fine.
        clean_env.setenv("RAZORPAY_KEY_ID", "rzp_test_publicid")
        assert make_settings().razorpay_key_id == "rzp_test_publicid"


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

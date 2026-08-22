"""GET /readiness — configuration readiness probe contract."""

import pytest


@pytest.fixture
def reset_settings_cache():
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield get_settings
    get_settings.cache_clear()


class TestReadinessDefaults:
    def test_ready_in_development_without_credentials(self, client) -> None:
        response = client.get("/readiness")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["environment"] == "development"
        assert body["issues"] == []
        assert body["config"] == {
            "database_configured": False,
            "auth_configured": False,
            "llm_configured": False,
            "razorpay_configured": False,
        }

    def test_readiness_never_leaks_secret_values(self, client, monkeypatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "super-secret-value")
        from app.core.config import get_settings

        get_settings.cache_clear()
        raw = client.get("/readiness").content.decode()
        assert "super-secret-value" not in raw
        get_settings.cache_clear()


class TestReadinessFailures:
    def test_503_when_database_url_scheme_invalid(
        self, client, monkeypatch, reset_settings_cache
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "mysql://u:p@localhost/db")
        response = client.get("/readiness")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert any("DATABASE_URL" in issue for issue in body["issues"])

    def test_503_in_production_without_required_secrets(
        self, client, monkeypatch, reset_settings_cache
    ) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        response = client.get("/readiness")
        assert response.status_code == 503
        issues = "\n".join(response.json()["issues"])
        assert "JWT_SECRET" in issues
        assert "LLM_API_KEY" in issues

    def test_production_with_all_secrets_is_ready(
        self, client, monkeypatch, reset_settings_cache
    ) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET", "x" * 32)
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        response = client.get("/readiness")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"


class TestHealthReadinessSeparation:
    def test_health_stays_ok_even_when_misconfigured(
        self, client, monkeypatch, reset_settings_cache
    ) -> None:
        # Liveness is dependency-free: a misconfiguration must not flip it.
        monkeypatch.setenv("DATABASE_URL", "mysql://u:p@localhost/db")
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/readiness").status_code == 503

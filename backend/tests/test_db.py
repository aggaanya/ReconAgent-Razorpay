"""Database infrastructure: engine, session dependency, connectivity probe.

Unit tests run without PostgreSQL. The integration tests at the bottom run
against a real PostgreSQL instance when ``RECONAGENT_TEST_DATABASE_URL`` is
set (e.g. ``postgresql+psycopg2://user:pw@localhost:5432/reconagent``) and are
skipped otherwise.
"""

import logging
import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import (
    DatabaseNotConfiguredError,
    check_database_connection,
    get_db,
    get_engine,
    get_session_factory,
    reset_engine,
)

TEST_DATABASE_URL = os.environ.get("RECONAGENT_TEST_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="RECONAGENT_TEST_DATABASE_URL not set; requires a running PostgreSQL",
)


@pytest.fixture(autouse=True)
def _isolated_engine():
    """Keep engine state isolated between tests."""
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def configured_url(monkeypatch) -> str:
    url = "postgresql+psycopg2://user:secret@localhost:5432/reconagent"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.config import get_settings

    get_settings.cache_clear()
    return url


@pytest.fixture
def set_database_url(monkeypatch):
    """Helper to point settings at a URL for one test."""

    def _set(url: str | None) -> None:
        if url is None:
            monkeypatch.setenv("DATABASE_URL", "")
        else:
            monkeypatch.setenv("DATABASE_URL", url)
        from app.core.config import get_settings

        get_settings.cache_clear()

    return _set


class TestEngine:
    def test_engine_initializes_from_database_url(self, configured_url) -> None:
        engine = get_engine()
        assert engine is get_engine()  # reused, never recreated per call
        rendered = str(engine.url)
        assert rendered.startswith("postgresql+psycopg2://")
        # Password is masked in the rendered URL.
        assert "secret" not in rendered

    def test_engine_requires_configuration(self, set_database_url) -> None:
        set_database_url(None)
        with pytest.raises(DatabaseNotConfiguredError):
            get_engine()

    def test_reset_engine_disposes_and_allows_recreation(
        self, configured_url
    ) -> None:
        first = get_engine()
        reset_engine()
        second = get_engine()
        assert first is not second


class TestSessionDependency:
    def test_get_db_yields_session_and_closes_it(
        self, configured_url, monkeypatch
    ) -> None:
        gen = get_db()
        session = next(gen)
        assert isinstance(session, Session)
        closed = False
        original_close = session.close

        def spy_close() -> None:
            nonlocal closed
            closed = True
            original_close()

        monkeypatch.setattr(session, "close", spy_close)
        gen.close()  # normal exit path -> finally -> close()
        assert closed

    def test_sessions_are_not_shared_between_requests(self, configured_url) -> None:
        first = next(get_db())
        second = next(get_db())
        assert first is not second

    def test_get_db_rolls_back_on_consumer_exception(
        self, configured_url, monkeypatch
    ) -> None:
        gen = get_db()
        session = next(gen)
        rolled_back = False
        original_rollback = session.rollback

        def spy_rollback() -> None:
            nonlocal rolled_back
            rolled_back = True
            original_rollback()

        monkeypatch.setattr(session, "rollback", spy_rollback)
        # Throw *into* the generator at the yield point, like a framework
        # tearing down a request after an unhandled error.
        with pytest.raises(RuntimeError, match="boom"):
            gen.throw(RuntimeError("boom"))
        assert rolled_back


class TestConnectivityProbe:
    def test_probe_returns_false_when_unreachable(self, set_database_url) -> None:
        set_database_url("postgresql+psycopg2://user:pw@127.0.0.1:1/none")
        assert check_database_connection() is False

    @requires_postgres
    def test_probe_returns_true_against_real_postgres(
        self, set_database_url
    ) -> None:
        set_database_url(TEST_DATABASE_URL)
        assert check_database_connection() is True

    def test_failure_logs_do_not_contain_credentials(
        self, set_database_url, caplog
    ) -> None:
        set_database_url("postgresql+psycopg2://user:secretpw@127.0.0.1:1/none")
        with caplog.at_level(logging.WARNING):
            assert check_database_connection() is False
        assert all("secretpw" not in r.getMessage() for r in caplog.records)


class TestReadinessDatabaseAwareness:
    def test_readiness_reports_available_when_database_reachable(
        self, client, monkeypatch, set_database_url
    ) -> None:
        from app.api import health as health_module

        set_database_url("postgresql+psycopg2://u:p@localhost:5432/db")
        monkeypatch.setattr(health_module, "check_database_connection", lambda: True)
        response = client.get("/readiness")
        body = response.json()
        assert response.status_code == 200
        assert body["database"] == "available"
        assert body["status"] == "ready"

    def test_readiness_sanitized_when_database_unreachable(
        self, client, monkeypatch, set_database_url
    ) -> None:
        from app.api import health as health_module

        password = "topsecretpw"
        set_database_url(f"postgresql+psycopg2://user:{password}@localhost:5432/db")
        monkeypatch.setattr(health_module, "check_database_connection", lambda: False)
        response = client.get("/readiness")
        body = response.json()
        assert response.status_code == 503
        assert body["database"] == "unavailable"
        assert any("Database" in issue for issue in body["issues"])
        assert password not in response.text  # no credentials or URL leaked

    def test_health_remains_ok_when_database_down(
        self, client, monkeypatch, set_database_url
    ) -> None:
        from app.api import health as health_module

        set_database_url("postgresql+psycopg2://u:p@localhost:5432/db")
        monkeypatch.setattr(health_module, "check_database_connection", lambda: False)
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/readiness").status_code == 503

    def test_readiness_not_configured_without_database_url(
        self, client, set_database_url
    ) -> None:
        set_database_url(None)
        response = client.get("/readiness")
        assert response.status_code == 200
        assert response.json()["database"] == "not_configured"


class TestRealPostgresIntegration:
    @requires_postgres
    def test_get_db_executes_query_end_to_end(self, set_database_url) -> None:
        set_database_url(TEST_DATABASE_URL)
        gen = get_db()
        session = next(gen)
        result = session.execute(text("SELECT 1")).scalar_one()
        gen.close()
        assert result == 1

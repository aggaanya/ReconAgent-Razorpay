"""Thin synchronization API tests.

The routes must delegate everything to the service layer: these tests pin
the HTTP contract (status codes, response shape, sanitized errors) while
backing the service with a fake fetcher and SQLite storage.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.sync import get_sync_service
from app.core.config import get_settings
from app.db.session import get_db
from app.integrations.razorpay.exceptions import (
    RazorpayConnectionError,
    RazorpayServerError,
)
from app.main import app
from tests.test_sync_service import FakeFetcher, payment, settlement


@pytest.fixture
def sync_client(session_factory):
    """TestClient whose sync service runs against fakes + SQLite."""
    fetcher = FakeFetcher(
        payment_pages=[[payment(1), payment(2)]],
        settlement_pages=[[settlement(1)]],
    )
    from app.services.sync import RazorpaySyncService

    service = RazorpaySyncService(
        fetcher=fetcher,
        session_factory=session_factory,
        page_size=100,
        max_pages=10,
        page_delay_seconds=0,
    )

    def _override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_sync_service] = lambda: service
    app.dependency_overrides[get_db] = _override_get_db
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client, service, session_factory
    app.dependency_overrides.pop(get_sync_service, None)
    app.dependency_overrides.pop(get_db, None)
    get_settings.cache_clear()


class TestTriggerEndpoint:
    def test_successful_run_returns_summary(self, sync_client):
        client, _, session_factory = sync_client
        response = client.post("/api/v1/sync/runs")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["payments"] == {
            "fetched": 2, "inserted": 2, "updated": 0, "skipped": 0,
        }
        assert body["settlements"] == {
            "fetched": 1, "inserted": 1, "updated": 0, "skipped": 0,
        }
        assert body["orders"] == {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0}
        assert body["refunds"] == {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0}
        # The recorded run is retrievable through the status endpoint.
        run_id = body["run_id"]
        follow_up = client.get(f"/api/v1/sync/runs/{run_id}")
        assert follow_up.status_code == 200
        assert follow_up.json()["run_id"] == run_id

    def test_partial_failure_surfaced_with_payments_kept(self, sync_client):
        client, service, _ = sync_client
        service._fetcher.errors = {
            ("settlements", 0): RazorpayServerError(
                "Razorpay API error (HTTP 500)"
            )
        }
        response = client.post("/api/v1/sync/runs")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial_failure"
        assert "HTTP 500" in body["error"]
        assert body["payments"]["inserted"] == 2

    def test_total_failure_maps_to_502(self, sync_client):
        client, service, _ = sync_client
        service._fetcher.errors = {
            ("payments", 0): RazorpayConnectionError(
                "Razorpay API request timed out after 10s"
            )
        }
        response = client.post("/api/v1/sync/runs")
        assert response.status_code == 502
        body = response.json()
        assert body["status"] == "failed"


class TestStatusEndpoint:
    def test_missing_run_is_404(self, sync_client):
        client, *_ = sync_client
        assert client.get("/api/v1/sync/runs/9999").status_code == 404

    def test_run_metadata_roundtrip(self, sync_client):
        client, _, session_factory = sync_client
        created = client.post("/api/v1/sync/runs").json()
        fetched = client.get(f"/api/v1/sync/runs/{created['run_id']}").json()
        assert fetched["status"] == "success"
        assert fetched["started_at"] is not None
        assert fetched["completed_at"] is not None


class TestUnconfiguredEnvironment:
    def test_missing_credentials_and_database_yield_503(self, monkeypatch):
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        get_settings.cache_clear()
        try:
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as excinfo:
                get_sync_service()
            assert excinfo.value.status_code == 503
        finally:
            get_settings.cache_clear()

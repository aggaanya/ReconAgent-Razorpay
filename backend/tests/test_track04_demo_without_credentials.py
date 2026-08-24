"""Track 04 demo contract: the full workflow runs WITHOUT Razorpay credentials.

The demo experience must never require ``RAZORPAY_KEY_ID`` /
``RAZORPAY_KEY_SECRET``:
- the application boots and reports ready with Razorpay unconfigured;
- ``POST /api/v1/ai/reconcile`` serves the canonical synthetic batch with
  zero external dependencies (no database, no Razorpay, LLM off);
- every Razorpay-backed endpoint keeps answering with its sanitized
  ``not_configured`` response instead of failing or leaking secrets.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas.reconciliation import ReconciliationStatus
from app.services.reconciliation_synthetic import CASE_DISTRIBUTION

# Canonical-batch expectations derived from the designed distribution
# (same derivation as test_api_ai_reconcile.py).
DEFAULT_TOTAL = sum(CASE_DISTRIBUTION.values())
DEFAULT_MATCHED = CASE_DISTRIBUTION[ReconciliationStatus.MATCHED]
DEFAULT_EXCEPTIONS = DEFAULT_TOTAL - DEFAULT_MATCHED
DEFAULT_MATCH_RATE = round(DEFAULT_MATCHED / DEFAULT_TOTAL * 100, 2)


@pytest.fixture
def no_razorpay_credentials(monkeypatch):
    """Remove Razorpay credentials and reset the settings cache."""
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestDemoWorksWithoutRazorpayCredentials:
    def test_app_boots_and_reports_ready(self, client) -> None:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        readiness = client.get("/readiness")
        assert readiness.status_code == 200
        body = readiness.json()
        assert body["status"] == "ready"
        assert body["issues"] == []
        assert body["config"]["razorpay_configured"] is False

    def test_synthetic_reconciliation_serves_full_report(
        self, client, no_razorpay_credentials
    ) -> None:
        response = client.post(
            "/api/v1/ai/reconcile",
            json={"source": "synthetic", "seed": 42, "size": 100, "explain": False},
        )
        assert response.status_code == 200
        report = response.json()

        assert report["status"] == "completed"
        assert report["total_records"] == DEFAULT_TOTAL
        assert report["matched_count"] == DEFAULT_MATCHED
        assert report["exception_count"] == DEFAULT_EXCEPTIONS
        assert report["match_rate"] == DEFAULT_MATCH_RATE
        # Ground truth never enters the serving path.
        assert report["accuracy"] is None
        assert report["errors"] == []
        assert len(report["exceptions"]) == DEFAULT_EXCEPTIONS
        for entry in report["exceptions"]:
            assert entry["exception_type"]
            assert entry["source_transaction_id"]

    def test_connection_endpoint_stays_sanitized_without_credentials(
        self, client, no_razorpay_credentials
    ) -> None:
        response = client.get("/api/v1/razorpay/connection")
        assert response.status_code == 503
        assert response.json() == {
            "connected": False,
            "environment": "unknown",
            "error": {
                "code": "not_configured",
                "message": "Razorpay credentials are not configured",
            },
        }

    def test_resource_endpoints_fail_closed_without_credentials(
        self, client, no_razorpay_credentials
    ) -> None:
        payments = client.get("/api/v1/razorpay/payments")
        assert payments.status_code == 503
        assert "not configured" in payments.json()["detail"]

        settlements = client.get("/api/v1/razorpay/settlements")
        assert settlements.status_code == 503
        assert "not configured" in settlements.json()["detail"]

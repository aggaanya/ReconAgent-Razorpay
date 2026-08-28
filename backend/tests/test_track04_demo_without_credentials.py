"""Demo contract: the full workflow runs WITHOUT any external credentials.

ReconAgent operates on a normalized internal financial data model;
synthetic data is provided for deterministic demos and evaluation.
External payment-provider ingestion is outside the core controller:

- the application boots and reports ready with zero credentials set;
- ``POST /api/v1/ai/reconcile`` serves the canonical synthetic batch with
  zero external dependencies (no database, no provider API, LLM off);
- the evaluation endpoint measures quality against isolated ground truth
  without any credentials either.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.schemas.reconciliation import ReconciliationStatus
from app.services.reconciliation_synthetic import CASE_DISTRIBUTION

# Canonical-batch expectations derived from the designed distribution
# (same derivation as test_api_ai_reconcile.py).
DEFAULT_TOTAL = sum(CASE_DISTRIBUTION.values())
DEFAULT_MATCHED = CASE_DISTRIBUTION[ReconciliationStatus.MATCHED]
DEFAULT_EXCEPTIONS = DEFAULT_TOTAL - DEFAULT_MATCHED
DEFAULT_MATCH_RATE = round(DEFAULT_MATCHED / DEFAULT_TOTAL * 100, 2)


@pytest.fixture
def no_external_credentials(monkeypatch):
    """Remove every optional credential and reset the settings cache."""
    # Empty process-level values override backend/.env; deleting them would
    # let pydantic-settings reload the developer's real credentials.
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestDemoWorksWithoutExternalCredentials:
    def test_app_boots_and_reports_ready(
        self, client, no_external_credentials
    ) -> None:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        readiness = client.get("/readiness")
        assert readiness.status_code == 200
        body = readiness.json()
        assert body["status"] == "ready"
        assert body["issues"] == []
        assert body["config"] == {
            "database_configured": False,
            "auth_configured": False,
            "llm_configured": False,
        }

    def test_synthetic_reconciliation_serves_full_report(
        self, client, no_external_credentials
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
            # Deterministic triage ships on every exception row.
            assert entry["severity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
            assert isinstance(entry["priority"], int)
            assert entry["recommended_action"]

    def test_evaluation_endpoint_needs_no_credentials(
        self, client, no_external_credentials
    ) -> None:
        response = client.get("/api/v1/ai/reconcile/evaluation")
        assert response.status_code == 200
        body = response.json()
        assert body["surface"] == "evaluation"
        assert body["total_records"] == DEFAULT_TOTAL

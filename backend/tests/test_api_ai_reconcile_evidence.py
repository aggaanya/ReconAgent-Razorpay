"""Evidence exposure tests: POST /api/v1/ai/reconcile.

Pins that the serving endpoint's exception list carries the structured
``evidence`` view on every entry, produced without any LLM (explain=false)
and identical across repeated runs. Additive contract only — the
pre-existing assertions in test_api_ai_reconcile.py are untouched.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def evidence_client(monkeypatch):
    """TestClient with no LLM configuration; explain=false needs none."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    import app.api.ai as ai_module

    saved = ai_module._agent
    ai_module._agent = None
    with TestClient(app) as test_client:
        yield test_client
    ai_module.reset_ai_agent()
    ai_module._agent = saved


EVIDENCE_KEYS = {
    "payment_id",
    "payment_amount_minor",
    "settlement_id",
    "settlement_amount_minor",
    "additional_settlement_ids",
    "refunds",
    "gross_amount_minor",
    "refunded_total_minor",
    "fee_minor",
    "tax_minor",
    "expected_settlement_minor",
    "actual_settlement_minor",
    "difference_minor",
    "status",
    "rules_triggered",
    "reason",
}


class TestEvidenceInServingResponse:
    def test_every_exception_carries_structured_evidence(self, evidence_client):
        response = evidence_client.post(
            "/api/v1/ai/reconcile",
            json={
                "source": "synthetic",
                "seed": 42,
                "size": 50,
                "explain": False,
            },
        )

        assert response.status_code == 200
        body = response.json()
        exceptions = body["exceptions"]
        assert len(exceptions) == body["exception_count"] > 0
        for entry in exceptions:
            evidence = entry["evidence"]
            assert evidence is not None
            assert EVIDENCE_KEYS <= set(evidence.keys())
            # The decision identity is mirrored inside the evidence.
            assert evidence["status"] == entry["status"]
            assert evidence["reason"] == entry["reason"]
            assert evidence["rules_triggered"][0] == entry["status"]
            for issue in entry.get("secondary_issues", []):
                assert issue in evidence["rules_triggered"]

    def test_evidence_is_deterministic_across_runs(self, evidence_client):
        payload = {
            "source": "synthetic",
            "seed": 42,
            "size": 50,
            "explain": False,
        }
        first = evidence_client.post(
            "/api/v1/ai/reconcile", json=payload
        ).json()
        second = evidence_client.post(
            "/api/v1/ai/reconcile", json=payload
        ).json()
        first_ev = [e["evidence"] for e in first["exceptions"]]
        second_ev = [e["evidence"] for e in second["exceptions"]]
        assert first_ev != []
        assert first_ev == second_ev

    def test_legacy_response_keys_remain_intact(self, evidence_client):
        """Backward compatibility: all pre-evidence fields still present."""
        response = evidence_client.post(
            "/api/v1/ai/reconcile",
            json={
                "source": "synthetic",
                "seed": 42,
                "size": 50,
                "explain": False,
            },
        )
        entry = response.json()["exceptions"][0]
        for key in (
            "source_transaction_id",
            "matched_transaction_id",
            "status",
            "exception_type",
            "expected_amount_minor",
            "actual_amount_minor",
            "difference_minor",
            "currency",
            "reason",
            "severity",
            "priority",
            "recommended_action",
        ):
            assert key in entry

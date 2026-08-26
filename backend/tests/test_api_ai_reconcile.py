"""AI reconcile API tests: POST /api/v1/ai/reconcile.

Pins the HTTP contract of the Track 04 reconciliation endpoint:
- 200 with exact deterministic metrics from the real engine (no DB, no
  Razorpay, no OpenAI — the LLM is always a stub);
- ``accuracy`` is null in every serving response by design;
- explain=true attaches the narrative, degrades to ``partial`` when the
  LLM fails, and answers 503 when no LLM key is configured;
- explain=false works with zero LLM configuration;
- clean 422s for out-of-range sizes, unknown sources, and empty focus
  prompts; OpenAPI documentation and secret/ground-truth leak prevention.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.ai.graph import FinanceIntelligenceAgent
from app.ai.llm import LLMConnectionError
from app.core.config import Settings, get_settings
from app.main import app
from app.schemas.reconciliation import ReconciliationStatus
from app.services.reconciliation_synthetic import CASE_DISTRIBUTION

# Canonical-batch expectations, derived from the designed distribution
# (per-100 weights) so distribution changes never orphan these pins.
DEFAULT_TOTAL = sum(CASE_DISTRIBUTION.values())
DEFAULT_MATCHED = CASE_DISTRIBUTION[ReconciliationStatus.MATCHED]
DEFAULT_EXCEPTIONS = DEFAULT_TOTAL - DEFAULT_MATCHED
DEFAULT_MATCH_RATE = round(DEFAULT_MATCHED / DEFAULT_TOTAL * 100, 2)
DEFAULT_UNRESOLVED = CASE_DISTRIBUTION[ReconciliationStatus.UNRESOLVED]


class StubLLM:
    """Scripted explainer double for the agent's narrative step."""

    def __init__(
        self,
        explanation="70 of 100 records reconciled cleanly.",
        explain_error=None,
    ) -> None:
        self.explanation = explanation
        self.explain_error = explain_error
        self.interpret_calls = []
        self.closed = False

    def complete_json(self, message, *, system_prompt=None):  # pragma: no cover
        raise AssertionError("reconcile endpoint never plans tools")

    def explain_signals(self, signals, *, question=None, system_prompt=None):
        self.interpret_calls.append(
            {"signals": signals, "question": question}
        )
        if self.explain_error is not None:
            raise self.explain_error
        from types import SimpleNamespace

        return SimpleNamespace(content=self.explanation)

    def close(self) -> None:
        self.closed = True


def install_agent(llm: StubLLM) -> None:
    import app.api.ai as ai_module

    ai_module._agent = FinanceIntelligenceAgent(llm)  # type: ignore[arg-type]


@pytest.fixture
def reconcile_client(monkeypatch):
    """TestClient with a pristine AI-agent singleton and no env config.

    The reconcile endpoint never touches the database, so no get_db
    override is needed — that itself is part of the contract.
    """
    import app.api.ai as ai_module

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    saved = ai_module._agent
    ai_module._agent = None
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    ai_module.reset_ai_agent()
    ai_module._agent = saved
    get_settings.cache_clear()


DEFAULT_BODY = {"source": "synthetic", "seed": 42, "size": 100}


class TestReconcileEndpointHappyPath:
    def test_default_batch_returns_exact_engine_metrics(self, reconcile_client):
        client = reconcile_client
        install_agent(StubLLM())  # explain defaults to true

        response = client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["answer"] is not None
        assert body["total_records"] == DEFAULT_TOTAL
        assert body["matched_count"] == DEFAULT_MATCHED
        assert body["exception_count"] == DEFAULT_EXCEPTIONS
        assert body["unresolved_count"] == DEFAULT_UNRESOLVED
        assert body["match_rate"] == DEFAULT_MATCH_RATE
        # Ground truth never enters the serving path.
        assert body["accuracy"] is None
        assert body["processing_time_ms"] >= 0
        assert body["throughput_records_per_second"] >= 0
        assert body["errors"] == []

    def test_exception_list_matches_breakdown_and_carries_fields(
        self, reconcile_client
    ):
        client = reconcile_client
        install_agent(StubLLM())

        body = client.post(
            "/api/v1/ai/reconcile", json=DEFAULT_BODY
        ).json()

        exceptions = body["exceptions"]
        assert len(exceptions) == DEFAULT_EXCEPTIONS
        breakdown: dict[str, int] = {}
        for entry in exceptions:
            breakdown[entry["exception_type"]] = (
                breakdown.get(entry["exception_type"], 0) + 1
            )
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
                # Financial components behind the expected settlement —
                # the LLM narrates these, so they must be present.
                "gross_amount_minor",
                "fee_minor",
                "tax_minor",
                "refunded_total_minor",
            ):
                assert key in entry
        assert breakdown == body["exception_breakdown"]

    def test_explain_false_skips_the_llm_entirely(self, reconcile_client):
        client = reconcile_client
        llm = StubLLM()
        install_agent(llm)

        response = client.post(
            "/api/v1/ai/reconcile",
            json={**DEFAULT_BODY, "explain": False},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["answer"] is None
        assert llm.interpret_calls == []  # never consulted

    def test_focus_question_is_forwarded_to_the_narrative_step(
        self, reconcile_client
    ):
        client = reconcile_client
        llm = StubLLM()
        install_agent(llm)

        response = client.post(
            "/api/v1/ai/reconcile",
            json={
                **DEFAULT_BODY,
                "explain": True,
                "question": "Which exceptions need human review first?",
            },
        )

        assert response.status_code == 200
        assert (
            llm.interpret_calls[0]["question"]
            == "Which exceptions need human review first?"
        )


class TestDegradationAndConfig:
    def test_llm_failure_degrades_to_partial_with_facts_intact(
        self, reconcile_client
    ):
        client = reconcile_client
        install_agent(StubLLM(explain_error=LLMConnectionError("down")))

        response = client.post(
            "/api/v1/ai/reconcile", json=DEFAULT_BODY
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial"
        assert body["answer"] is None
        assert any("narrative" in e.lower() for e in body["errors"])
        assert "down" not in " ".join(body["errors"]).lower()
        assert "llmconnectionerror" not in " ".join(body["errors"]).lower()
        # The deterministic report survived the failure untouched.
        assert body["total_records"] == DEFAULT_TOTAL
        assert len(body["exceptions"]) == DEFAULT_EXCEPTIONS

    def test_missing_llm_key_answers_503_when_explanation_requested(
        self, reconcile_client, monkeypatch
    ):
        client = reconcile_client
        monkeypatch.setattr(
            "app.api.ai.get_settings",
            lambda: Settings(_env_file=None, llm_api_key=None),
        )

        response = client.post(
            "/api/v1/ai/reconcile",
            json={**DEFAULT_BODY, "explain": True},
        )

        assert response.status_code == 503
        assert "LLM_API_KEY" in response.json()["detail"]

    def test_missing_llm_key_still_serves_facts_without_explanation(
        self, reconcile_client, monkeypatch
    ):
        """Deployment without any AI key still gets the full report."""
        client = reconcile_client
        monkeypatch.setattr(
            "app.api.ai.get_settings",
            lambda: Settings(_env_file=None, llm_api_key=None),
        )

        response = client.post(
            "/api/v1/ai/reconcile",
            json={**DEFAULT_BODY, "explain": False},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["match_rate"] == DEFAULT_MATCH_RATE

    def test_endpoint_never_touches_the_database(self, monkeypatch):
        """No DATABASE_URL anywhere: the route must not depend on get_db."""
        import app.api.ai as ai_module

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        ai_module._agent = FinanceIntelligenceAgent(StubLLM())  # type: ignore[arg-type]
        get_settings.cache_clear()
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/reconcile",
                    json={**DEFAULT_BODY, "explain": False},
                )
        finally:
            ai_module.reset_ai_agent()
        assert response.status_code == 200


class TestRequestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {**DEFAULT_BODY, "size": 10},  # below the Track-04 floor
            {**DEFAULT_BODY, "size": 5001},  # above the cap
            {**DEFAULT_BODY, "seed": -1},
            {**DEFAULT_BODY, "source": "production_database"},
            {**DEFAULT_BODY, "records": "inject-me"},
            {**DEFAULT_BODY, "question": "   \t "},  # whitespace-only focus
            {**DEFAULT_BODY, "question": 12345},
        ],
    )
    def test_invalid_requests_yield_422(self, reconcile_client, payload):
        response = reconcile_client.post("/api/v1/ai/reconcile", json=payload)
        assert response.status_code == 422

    def test_defaults_run_the_canonical_track_04_batch(self, reconcile_client):
        install_agent(StubLLM())
        body = reconcile_client.post(
            "/api/v1/ai/reconcile", json={}
        ).json()
        assert body["total_records"] == 100


class TestContractAndSecurity:
    def test_openapi_documents_the_route_and_nullable_accuracy(
        self, reconcile_client
    ):
        spec = reconcile_client.get("/openapi.json").json()
        assert "/api/v1/ai/reconcile" in spec["paths"]
        ref = spec["paths"]["/api/v1/ai/reconcile"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]["$ref"]
        request_schema = spec["components"]["schemas"][ref.split("/")[-1]]
        assert request_schema.get("required", []) == []
        size_prop = request_schema["properties"]["size"]
        assert size_prop["minimum"] == 50
        assert size_prop["maximum"] == 5000

        response_ref = spec["paths"]["/api/v1/ai/reconcile"]["post"][
            "responses"
        ]["200"]["content"]["application/json"]["schema"]["$ref"]
        response_schema = spec["components"]["schemas"][
            response_ref.split("/")[-1]
        ]
        accuracy_prop = response_schema["properties"]["accuracy"]
        is_nullable = accuracy_prop.get("nullable") is True or any(
            option.get("type") in ("number", "null")
            for option in accuracy_prop.get("anyOf", [])
        )
        assert is_nullable

    def test_response_never_contains_secrets_or_ground_truth(
        self, reconcile_client
    ):
        install_agent(StubLLM(explanation="Report looks consistent."))
        response = reconcile_client.post(
            "/api/v1/ai/reconcile", json=DEFAULT_BODY
        )
        encoded = json.dumps(response.json()).lower()
        assert response.status_code == 200
        assert "sk-" not in encoded
        assert "api_key" not in encoded
        assert "ground_truth" not in encoded
        assert "expected_status" not in encoded
        assert "case_id" not in encoded

    def test_identical_requests_produce_identical_reports(
        self, reconcile_client
    ):
        install_agent(StubLLM())
        first = reconcile_client.post(
            "/api/v1/ai/reconcile", json=DEFAULT_BODY
        ).json()
        second = reconcile_client.post(
            "/api/v1/ai/reconcile", json=DEFAULT_BODY
        ).json()
        for payload in (first, second):
            payload.pop("processing_time_ms")
            payload.pop("throughput_records_per_second")
            payload.pop("answer")  # stub is deterministic anyway
        assert first == second

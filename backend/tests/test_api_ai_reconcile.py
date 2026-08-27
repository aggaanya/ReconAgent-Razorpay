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
from app.core.cache import reconciliation_cache
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
        self.interpret_calls.append({"signals": signals, "question": question})
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
    from app.core.cache import reconciliation_cache, signal_cache

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    saved = ai_module._agent
    ai_module._agent = None
    get_settings.cache_clear()
    reconciliation_cache.clear()
    signal_cache.clear()
    with TestClient(app) as test_client:
        yield test_client
    ai_module.reset_ai_agent()
    ai_module._agent = saved
    get_settings.cache_clear()
    reconciliation_cache.clear()
    signal_cache.clear()


DEFAULT_BODY = {"source": "synthetic", "seed": 42, "size": 100}


class TestReconcileEndpointHappyPath:
    def test_identical_facts_only_requests_use_deterministic_cache(
        self, reconcile_client, monkeypatch
    ):
        import app.api.ai as ai_module

        reconciliation_cache.clear()
        calls = 0
        original_run = ai_module.RECONCILE_TRANSACTION_TOOL.run

        def counted_run(session, payload):
            nonlocal calls
            calls += 1
            return original_run(session, payload)

        monkeypatch.setattr(ai_module.RECONCILE_TRANSACTION_TOOL, "run", counted_run)

        first = reconcile_client.post(
            "/api/v1/ai/reconcile",
            json={**DEFAULT_BODY, "explain": False},
        )
        second = reconcile_client.post(
            "/api/v1/ai/reconcile",
            json={**DEFAULT_BODY, "explain": False},
        )

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert calls == 1

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

        body = client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY).json()

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

    def test_focus_question_is_forwarded_to_the_narrative_step(self, reconcile_client):
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
    def test_llm_failure_degrades_to_partial_with_facts_intact(self, reconcile_client):
        client = reconcile_client
        install_agent(StubLLM(explain_error=LLMConnectionError("down")))

        response = client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY)

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
        body = reconcile_client.post("/api/v1/ai/reconcile", json={}).json()
        assert body["total_records"] == 100


class TestContractAndSecurity:
    def test_openapi_documents_the_route_and_nullable_accuracy(self, reconcile_client):
        spec = reconcile_client.get("/openapi.json").json()
        assert "/api/v1/ai/reconcile" in spec["paths"]
        ref = spec["paths"]["/api/v1/ai/reconcile"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        request_schema = spec["components"]["schemas"][ref.split("/")[-1]]
        assert request_schema.get("required", []) == []
        size_prop = request_schema["properties"]["size"]
        assert size_prop["minimum"] == 50
        assert size_prop["maximum"] == 5000

        response_ref = spec["paths"]["/api/v1/ai/reconcile"]["post"]["responses"][
            "200"
        ]["content"]["application/json"]["schema"]["$ref"]
        response_schema = spec["components"]["schemas"][response_ref.split("/")[-1]]
        accuracy_prop = response_schema["properties"]["accuracy"]
        is_nullable = accuracy_prop.get("nullable") is True or any(
            option.get("type") in ("number", "null")
            for option in accuracy_prop.get("anyOf", [])
        )
        assert is_nullable

    def test_response_never_contains_secrets_or_ground_truth(self, reconcile_client):
        install_agent(StubLLM(explanation="Report looks consistent."))
        response = reconcile_client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY)
        encoded = json.dumps(response.json()).lower()
        assert response.status_code == 200
        assert "sk-" not in encoded
        assert "api_key" not in encoded
        assert "ground_truth" not in encoded
        assert "expected_status" not in encoded
        assert "case_id" not in encoded

    def test_identical_requests_produce_identical_reports(self, reconcile_client):
        install_agent(StubLLM())
        first = reconcile_client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY).json()
        second = reconcile_client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY).json()
        for payload in (first, second):
            payload.pop("processing_time_ms")
            payload.pop("throughput_records_per_second")
            payload.pop("answer")  # stub is deterministic anyway
        assert first == second


class TestNarrativeReceivesDeterministicSignals:
    """Verify that the AI narrative layer receives and preserves exact
    deterministic values from the reconciliation engine — no recalculation,
    no invention, no omission."""

    def test_signals_contain_all_required_reconciliation_values(
        self, reconcile_client
    ):
        """The signals dict passed to the LLM must include every key the
        prompt references: total_records, matched_count, exception_count,
        match_rate, unresolved_count, exception_summary, and
        sample_exceptions."""
        llm = StubLLM()
        install_agent(llm)

        reconcile_client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY)

        assert len(llm.interpret_calls) == 1
        signals = llm.interpret_calls[0]["signals"]

        # Core deterministic metrics from the engine
        data = signals["data"]
        assert data["total_records"] == DEFAULT_TOTAL
        assert data["matched_count"] == DEFAULT_MATCHED
        assert data["exception_count"] == DEFAULT_EXCEPTIONS
        assert data["unresolved_count"] == DEFAULT_UNRESOLVED
        assert data["match_rate"] == DEFAULT_MATCH_RATE

        # Dataset identity
        assert signals["dataset"]["source"] == "synthetic"
        assert signals["dataset"]["seed"] == 42
        assert signals["dataset"]["size"] == 100

        # Exception summary with exposure and severity breakdown
        assert signals["exception_summary"] is not None
        es = signals["exception_summary"]
        assert "total_financial_exposure_minor" in es
        assert "critical_count" in es
        assert "high_count" in es
        assert "medium_count" in es
        assert "low_count" in es
        assert "top_exception_categories" in es

        # Sample exceptions carry raw minor-unit values only
        assert isinstance(signals["sample_exceptions"], list)
        if signals["sample_exceptions"]:
            for exc in signals["sample_exceptions"]:
                assert "status" in exc
                assert "severity" in exc
                assert "financial_impact_minor" in exc

    def test_signals_do_not_contain_pre_computed_human_readable(
        self, reconcile_client
    ):
        """The signals must NOT contain pre-computed human-readable
        financial amounts. The LLM should format these from raw minor-unit
        values per the prompt instructions."""
        llm = StubLLM()
        install_agent(llm)

        reconcile_client.post("/api/v1/ai/reconcile", json=DEFAULT_BODY)

        signals = llm.interpret_calls[0]["signals"]

        # exposure_human_readable must not exist — the LLM formats from raw values
        assert "exposure_human_readable" not in signals

        # sample exceptions must not have pre-formatted display fields
        for exc in signals.get("sample_exceptions", []):
            assert "financial_impact_display" not in exc

    def test_exact_values_match_engine_output(self, reconcile_client):
        """Every number in the signals must exactly match the deterministic
        engine output — no rounding, no conversion, no reinterpretation."""
        llm = StubLLM()
        install_agent(llm)

        response = reconcile_client.post(
            "/api/v1/ai/reconcile", json=DEFAULT_BODY
        ).json()

        signals = llm.interpret_calls[0]["signals"]

        # Core metrics are byte-identical between response and signals
        assert signals["data"]["total_records"] == response["total_records"]
        assert signals["data"]["matched_count"] == response["matched_count"]
        assert signals["data"]["exception_count"] == response["exception_count"]
        assert signals["data"]["unresolved_count"] == response["unresolved_count"]
        assert signals["data"]["match_rate"] == response["match_rate"]

        # Exception summary exposure matches
        if response.get("exception_summary"):
            assert (
                signals["exception_summary"]["total_financial_exposure_minor"]
                == response["exception_summary"]["total_financial_exposure_minor"]
            )
            assert (
                signals["exception_summary"]["critical_count"]
                == response["exception_summary"]["critical_count"]
            )
            assert (
                signals["exception_summary"]["high_count"]
                == response["exception_summary"]["high_count"]
            )
            assert (
                signals["exception_summary"]["medium_count"]
                == response["exception_summary"]["medium_count"]
            )
            assert (
                signals["exception_summary"]["low_count"]
                == response["exception_summary"]["low_count"]
            )

    def test_narrative_uses_exact_values_not_invented_numbers(
        self, reconcile_client
    ):
        """The LLM explanation must reference values that exist in the
        signals — it must not introduce counts, percentages, or monetary
        amounts that are not in the supplied data."""
        explanation = (
            f"Match rate is {DEFAULT_MATCH_RATE}% across {DEFAULT_TOTAL} records. "
            f"{DEFAULT_EXCEPTIONS} exceptions were found, with {DEFAULT_UNRESOLVED} "
            f"unresolved. The finance team should review critical and high severity items."
        )
        llm = StubLLM(explanation=explanation)
        install_agent(llm)

        response = reconcile_client.post(
            "/api/v1/ai/reconcile", json=DEFAULT_BODY
        ).json()

        assert response["answer"] is not None
        # The narrative must contain the exact match rate and total records
        assert str(DEFAULT_MATCH_RATE) in response["answer"]
        assert str(DEFAULT_TOTAL) in response["answer"]
        assert str(DEFAULT_EXCEPTIONS) in response["answer"]
        assert str(DEFAULT_UNRESOLVED) in response["answer"]

    def test_question_forwarded_without_altering_signals(
        self, reconcile_client
    ):
        """A custom focus question must not alter the deterministic
        values in the signals."""
        llm = StubLLM()
        install_agent(llm)

        reconcile_client.post(
            "/api/v1/ai/reconcile",
            json={
                **DEFAULT_BODY,
                "question": "Which critical exceptions need immediate attention?",
            },
        )

        signals = llm.interpret_calls[0]["signals"]
        assert signals["data"]["total_records"] == DEFAULT_TOTAL
        assert signals["data"]["matched_count"] == DEFAULT_MATCHED
        assert signals["data"]["exception_count"] == DEFAULT_EXCEPTIONS
        assert signals["data"]["match_rate"] == DEFAULT_MATCH_RATE

    def test_no_exposure_human_readable_in_any_signals_payload(
        self, reconcile_client
    ):
        """Verify that no pre-computed human-readable financial amount
        leaks into the signals regardless of batch configuration."""
        llm = StubLLM()
        install_agent(llm)

        reconcile_client.post(
            "/api/v1/ai/reconcile",
            json={"source": "synthetic", "seed": 99, "size": 50},
        )

        signals = llm.interpret_calls[0]["signals"]

        # Deep scan: no value in the signals should be a pre-formatted
        # currency string like "INR 1,234.56"
        import re
        currency_pattern = re.compile(r"[A-Z]{3}\s[\d,]+\.\d{2}")
        signals_json = json.dumps(signals)
        assert not currency_pattern.search(signals_json), (
            "Signals contain pre-formatted currency strings; all financial "
            "values must be raw minor-unit integers"
        )

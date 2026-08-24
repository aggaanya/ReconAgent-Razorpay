"""AI chat API tests: POST /api/v1/ai/chat.

Pins the HTTP contract of the natural-language finance endpoint:
- 200 with grounded facts (exact minor-unit integers) + LLM narrative,
  ``partial`` degradation when interpretation fails, and safe mapping of
  graph-level ``failed`` outcomes;
- clean 422s for blank/missing/over-long questions;
- 503 when the LLM integration or the database is unconfigured;
- OpenAPI documentation and secret-leak prevention.

The LLM is always a stub; tools run against seeded SQLite storage via the
real LangGraph stack, so the deterministic numbers asserted here come from
the actual Finance Intelligence Engine.
"""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.ai.graph import FinanceAgentResult, FinanceIntelligenceAgent
from app.ai.llm import LLMConnectionError, LLMError
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.schemas.ai import MAX_QUESTION_LENGTH


class StubLLM:
    """Scripted LLMService double: planned tools + canned interpretation."""

    def __init__(
        self,
        plan_payload=None,
        plan_error=None,
        explanation="Revenue looks healthy based on the retrieved signals.",
        explain_error=None,
    ) -> None:
        self.plan_payload = plan_payload
        self.plan_error = plan_error
        self.explanation = explanation
        self.explain_error = explain_error
        self.closed = False

    def complete_json(self, message, *, system_prompt=None):
        if self.plan_error is not None:
            raise self.plan_error
        return self.plan_payload

    def explain_signals(self, signals, *, question=None, system_prompt=None):
        if self.explain_error is not None:
            raise self.explain_error
        from types import SimpleNamespace

        return SimpleNamespace(content=self.explanation)

    def close(self) -> None:
        self.closed = True


class LLMRateLimitStub(LLMError):
    """Scripted persistent-rate-limit failure for planner calls."""


def install_agent(llm: StubLLM) -> FinanceIntelligenceAgent:
    """Build a real graph-backed agent around the stub and publish it."""
    import app.api.ai as ai_module

    agent = FinanceIntelligenceAgent(llm)  # type: ignore[arg-type]
    ai_module._agent = agent
    return agent


def seed_payment(session, *, amount_minor: int, status: str = "captured"):
    from app.db.mappers import payment_from_normalized
    from app.db.repositories import PaymentRepository
    from app.schemas import NormalizedPayment

    row = payment_from_normalized(
        NormalizedPayment(
            provider="razorpay",
            external_id=f"pay_chat{amount_minor}",
            amount_minor=amount_minor,
            currency="INR",
            status=status,
            method="upi",
            order_id=f"order_chat{amount_minor}",
            created_at=datetime.now(tz=timezone.utc),
        )
    )
    PaymentRepository(session).upsert(row)


@pytest.fixture
def ai_client(session_factory, monkeypatch):
    """TestClient wired to SQLite with a pristine AI-agent singleton."""
    import app.api.ai as ai_module

    def _override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    saved = ai_module._agent
    ai_module._agent = None
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client, session_factory
    app.dependency_overrides.pop(get_db, None)
    ai_module.reset_ai_agent()
    ai_module._agent = saved
    get_settings.cache_clear()


REVENUE_PLAN = {"tools": [{"tool": "revenue", "arguments": {}}]}


class TestChatEndpointHappyPath:
    def test_question_returns_grounded_answer_and_exact_numbers(
        self, ai_client
    ):
        client, session_factory = ai_client
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        install_agent(llm)
        session = session_factory()
        try:
            seed_payment(session, amount_minor=250_000)
            session.commit()
        finally:
            session.close()

        response = client.post(
            "/api/v1/ai/chat", json={"question": "What was my revenue?"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["answer"] == llm.explanation
        assert body["selected_tools"] == ["revenue"]
        assert body["errors"] == []
        assert body["tool_errors"] == {}
        revenue_envelope = body["tool_results"]["revenue"]
        # Exact deterministic engine output — never an LLM-computed value.
        assert revenue_envelope["data"]["gross_revenue_minor"] == 250_000
        assert isinstance(revenue_envelope["data"]["gross_revenue_minor"], int)
        assert isinstance(body["financial_signals"], list)
        assert body["question"] == "What was my revenue?"

    def test_partial_status_when_interpretation_fails(self, ai_client):
        client, _ = ai_client
        llm = StubLLM(
            plan_payload=REVENUE_PLAN,
            explain_error=LLMConnectionError("provider down"),
        )
        install_agent(llm)

        response = client.post(
            "/api/v1/ai/chat", json={"question": "What was my revenue?"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial"
        assert body["answer"] is None
        # The facts survive the interpretation failure.
        assert "revenue" in body["tool_results"]
        assert any("Interpretation" in e for e in body["errors"])

    def test_graph_failed_outcome_maps_to_structured_response(self, ai_client):
        """A run that retrieves nothing stays HTTP 200 with status=failed."""
        client, _ = ai_client

        class FailedAgent:
            def run(self, session, question):
                return FinanceAgentResult(
                    question=question,
                    status="failed",
                    errors=["No data could be retrieved."],
                )

            def close(self):
                pass

        import app.api.ai as ai_module

        ai_module._agent = FailedAgent()  # type: ignore[assignment]

        response = client.post("/api/v1/ai/chat", json={"question": "hi"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert body["answer"] is None
        assert body["tool_results"] == {}
        assert body["errors"] == ["No data could be retrieved."]

    def test_keyword_fallback_still_answers_when_planner_llm_down(
        self, ai_client
    ):
        client, session_factory = ai_client
        llm = StubLLM(plan_payload={}, plan_error=LLMRateLimitStub())
        install_agent(llm)
        session = session_factory()
        try:
            seed_payment(session, amount_minor=100)
            session.commit()
        finally:
            session.close()

        response = client.post(
            "/api/v1/ai/chat", json={"question": "give me an overview"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["selection_source"] == "keyword_fallback"
        assert body["selected_tools"] == ["financial_summary"]


class TestChatRequestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"question": ""},
            {"question": "   \n\t  "},
            {"question": "x" * (MAX_QUESTION_LENGTH + 1)},
            {"question": 42},
        ],
    )
    def test_invalid_questions_yield_422(self, ai_client, payload):
        client, _ = ai_client
        install_agent(StubLLM(plan_payload=REVENUE_PLAN))
        response = client.post("/api/v1/ai/chat", json=payload)
        assert response.status_code == 422

    def test_surrounding_whitespace_is_trimmed_not_rejected(self, ai_client):
        client, _ = ai_client
        install_agent(StubLLM(plan_payload=REVENUE_PLAN))
        response = client.post(
            "/api/v1/ai/chat", json={"question": "  What was my revenue?  "}
        )
        assert response.status_code == 200
        assert response.json()["question"] == "What was my revenue?"


class TestUnconfiguredDependencies:
    def test_missing_llm_key_answers_503_with_actionable_detail(
        self, ai_client, monkeypatch
    ):
        client, _ = ai_client
        # Deterministic settings with no LLM key regardless of machine env.
        monkeypatch.setattr(
            "app.api.ai.get_settings",
            lambda: Settings(_env_file=None, llm_api_key=None),
        )

        response = client.post(
            "/api/v1/ai/chat", json={"question": "What was my revenue?"}
        )

        assert response.status_code == 503
        assert "LLM_API_KEY" in response.json()["detail"]

    def test_unconfigured_database_answers_503(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import app.api.ai as ai_module

        saved = ai_module._agent
        ai_module._agent = None
        get_settings.cache_clear()
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/chat", json={"question": "What was my revenue?"}
                )
        finally:
            ai_module.reset_ai_agent()
            ai_module._agent = saved
            get_settings.cache_clear()
        assert response.status_code == 503


class TestContractAndSecurity:
    def test_openapi_documents_the_route_and_schema(self, ai_client):
        client, _ = ai_client
        spec = client.get("/openapi.json").json()
        assert "/api/v1/ai/chat" in spec["paths"]
        request_schema_ref = spec["paths"]["/api/v1/ai/chat"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]["$ref"]
        schema_name = request_schema_ref.split("/")[-1]
        schema = spec["components"]["schemas"][schema_name]
        assert schema["required"] == ["question"]
        assert schema["properties"]["question"]["type"] == "string"

    def test_response_never_contains_secret_material(self, ai_client):
        client, _ = ai_client
        llm = StubLLM(plan_payload=REVENUE_PLAN)
        install_agent(llm)

        response = client.post(
            "/api/v1/ai/chat", json={"question": "What was my revenue?"}
        )

        assert response.status_code == 200
        encoded = json.dumps(response.json())
        assert "sk-" not in encoded
        assert "api_key" not in encoded.lower()

    def test_answer_is_words_only_while_numbers_stay_in_tool_results(
        self, ai_client
    ):
        client, _ = ai_client
        llm = StubLLM(
            plan_payload=REVENUE_PLAN,
            explanation=(
                "The data shows gross revenue of 250000 minor units this "
                "period; a possible explanation for the level is seasonal "
                "demand, but the available data does not establish cause."
            ),
        )
        install_agent(llm)

        response = client.post(
            "/api/v1/ai/chat",
            json={"question": "Why is my revenue at this level?"},
        )

        body = response.json()
        # Narrative and facts travel on separate channels.
        assert body["answer"] == llm.explanation
        assert body["tool_results"]["revenue"]["tool"] == "revenue"


class TestAgentLifecycle:
    def test_reset_ai_agent_closes_and_discards_singleton(self):
        import app.api.ai as ai_module

        llm = StubLLM(plan_payload=REVENUE_PLAN)
        agent = FinanceIntelligenceAgent(llm)  # type: ignore[arg-type]
        ai_module._agent = agent
        ai_module.reset_ai_agent()
        assert llm.closed is True
        assert ai_module._agent is None

    def test_get_ai_agent_builds_once_and_reuses(self, monkeypatch):
        import app.api.ai as ai_module

        ai_module._agent = None
        settings = Settings(_env_file=None, llm_api_key="cfg-key")
        calls = {"count": 0}

        def fake_settings():
            calls["count"] += 1
            return settings

        monkeypatch.setattr(ai_module, "get_settings", fake_settings)
        first = ai_module.get_ai_agent()
        second = ai_module.get_ai_agent()
        assert first is second
        # Settings consulted only until an agent exists; the built agent
        # (and its pooled LLM client) is then reused for every request.
        assert calls["count"] == 1
        ai_module.reset_ai_agent()
        third = ai_module.get_ai_agent()
        assert third is not first
        assert calls["count"] == 2  # rebuilt after reset
        ai_module.reset_ai_agent()

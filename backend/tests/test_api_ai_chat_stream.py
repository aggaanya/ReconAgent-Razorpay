"""Contract tests for the SSE AI chat endpoint."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.ai.graph import FinanceAgentResult, FinanceIntelligenceAgent
from app.api.ai import get_ai_agent
from app.core.config import get_settings
from app.db.session import get_db
from app.main import app


class StubStreamingAgent:
    def __init__(self, events):
        self.events = events

    def stream(self, session_factory, question):
        yield from self.events


def _noop_db():
    yield None


def test_chat_stream_returns_sse_and_completion():
    result = FinanceAgentResult(
        question="What changed?",
        status="completed",
        interpretation="The data shows stable revenue.",
    )
    app.dependency_overrides[get_ai_agent] = lambda: StubStreamingAgent(
        [("token", "The data shows "), ("token", "stable revenue."), ("result", result)]
    )
    app.dependency_overrides[get_db] = _noop_db
    get_settings.cache_clear()
    try:
        response = TestClient(app).post(
            "/api/v1/ai/chat/stream",
            json={"question": "What changed?"},
        )
    finally:
        app.dependency_overrides.pop(get_ai_agent, None)
        app.dependency_overrides.pop(get_db, None)
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: token\ndata: {"content": "The data shows "}' in response.text
    assert "event: complete" in response.text
    assert '"answer": "The data shows stable revenue."' in response.text


def test_chat_stream_returns_controlled_error():
    app.dependency_overrides[get_ai_agent] = lambda: StubStreamingAgent(
        [("error", RuntimeError("provider details must not leak"))]
    )
    app.dependency_overrides[get_db] = _noop_db
    get_settings.cache_clear()
    try:
        response = TestClient(app).post(
            "/api/v1/ai/chat/stream",
            json={"question": "What changed?"},
        )
    finally:
        app.dependency_overrides.pop(get_ai_agent, None)
        app.dependency_overrides.pop(get_db, None)
        get_settings.cache_clear()

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "provider details must not leak" not in response.text
    assert "AI response generation is unavailable." in response.text


class StubLLM:
    """Scripted LLMService double for the real graph-backed agent."""

    def __init__(self, explanation="Revenue looks healthy."):
        self.explanation = explanation

    def complete_json(self, message, *, system_prompt=None):
        return {"tools": [{"tool": "revenue", "arguments": {}}]}

    def explain_signals(self, signals, *, question=None, system_prompt=None):
        from types import SimpleNamespace

        return SimpleNamespace(content=self.explanation)

    def close(self):
        pass


@pytest.fixture
def stream_client(session_factory, monkeypatch):
    """Real graph-backed agent over seeded SQLite, stream endpoint wired."""
    import app.api.ai as ai_module

    def _override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _seed(session):
        from app.db.mappers import payment_from_normalized
        from app.db.repositories import PaymentRepository
        from app.schemas import NormalizedPayment

        PaymentRepository(session).upsert(
            payment_from_normalized(
                NormalizedPayment(
                    provider="razorpay",
                    external_id="pay_stream1",
                    amount_minor=250_000,
                    currency="INR",
                    status="captured",
                    method="upi",
                    order_id="order_stream1",
                    created_at=datetime.now(tz=timezone.utc),
                )
            )
        )
        session.commit()

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    saved = ai_module._agent
    ai_module._agent = None
    get_settings.cache_clear()
    session = session_factory()
    try:
        _seed(session)
    finally:
        session.close()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)
    ai_module.reset_ai_agent()
    ai_module._agent = saved
    get_settings.cache_clear()


def test_real_agent_stream_emits_tokens_and_complete(stream_client):
    import app.api.ai as ai_module

    llm = StubLLM(explanation="Your revenue reached ₹2,50,000 this month.")
    ai_module._agent = FinanceIntelligenceAgent(llm)  # type: ignore[arg-type]
    response = stream_client.post(
        "/api/v1/ai/chat/stream",
        json={"question": "What was my revenue this month?"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: token\ndata: {"content": "Your revenue reached' in response.text
    assert "event: complete" in response.text
    assert '"answer": "Your revenue reached' in response.text

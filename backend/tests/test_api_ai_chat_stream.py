"""Contract tests for the SSE AI chat endpoint."""

from fastapi.testclient import TestClient

from app.ai.graph import FinanceAgentResult
from app.api.ai import get_ai_agent
from app.main import app


class StubStreamingAgent:
    def __init__(self, events):
        self.events = events

    def stream(self, session_factory, question):
        yield from self.events


def test_chat_stream_returns_sse_and_completion():
    result = FinanceAgentResult(
        question="What changed?",
        status="completed",
        interpretation="The data shows stable revenue.",
    )
    app.dependency_overrides[get_ai_agent] = lambda: StubStreamingAgent(
        [("token", "The data shows "), ("token", "stable revenue."), ("result", result)]
    )
    try:
        response = TestClient(app).post(
            "/api/v1/ai/chat/stream",
            json={"question": "What changed?"},
        )
    finally:
        app.dependency_overrides.pop(get_ai_agent, None)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: token\ndata: {"content": "The data shows "}' in response.text
    assert "event: complete" in response.text
    assert '"answer": "The data shows stable revenue."' in response.text


def test_chat_stream_returns_controlled_error():
    app.dependency_overrides[get_ai_agent] = lambda: StubStreamingAgent(
        [("error", RuntimeError("provider details must not leak"))]
    )
    try:
        response = TestClient(app).post(
            "/api/v1/ai/chat/stream",
            json={"question": "What changed?"},
        )
    finally:
        app.dependency_overrides.pop(get_ai_agent, None)

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "provider details must not leak" not in response.text
    assert "AI response generation is unavailable." in response.text

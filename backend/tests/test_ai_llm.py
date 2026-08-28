"""LLM service contract tests (fully mocked — no network in this suite).

Covers:

- construction guards (unconfigured key, blank model, bad knobs)
- ``from_settings`` wiring and the ``llm_configured`` boundary
- request shape: system prompt (with the no-calculation boundary),
  user message, model, and JSON response mode
- structured-signal framing: signals serialized verbatim, never altered
- typed error translation from OpenAI SDK exceptions
- empty/malformed response handling

A separate opt-in live test runs only when RECONAGENT_LLM_LIVE_TEST=1
AND a configured LLM_API_KEY are present; the default suite stays
deterministic.
"""

import json
import os
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from app.ai.llm import (
    DEFAULT_FINANCE_SYSTEM_PROMPT,
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMCompletionResult,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMError,
    LLMInvalidResponseError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMServerError,
    LLMService,
)
from app.core.config import Settings

API_KEY = "test-key-not-a-secret"
PROVIDER_URL = "https://llm.example.test/v1/chat/completions"


# --- stubs -------------------------------------------------------------------


class StubCompletions:
    """Records create() kwargs and replays a scripted response/error."""

    def __init__(self, responder):
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._responder(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def stub_service(responder, **service_kwargs) -> tuple[LLMService, StubCompletions]:
    completions = StubCompletions(responder)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = LLMService(
        api_key=API_KEY, model="gpt-test", timeout_seconds=5, max_retries=0,
        client=client, **service_kwargs,
    )
    return service, completions


def fake_response(
    content="all good",
    *,
    model="gpt-test",
    finish_reason="stop",
    usage=None,
):
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason
    )
    return SimpleNamespace(model=model, choices=[choice], usage=usage)


def status_error(error_cls, status_code: int) -> Exception:
    return error_cls(
        f"provider said {status_code}",
        response=httpx.Response(
            status_code, request=httpx.Request("POST", PROVIDER_URL)
        ),
        body=None,
    )


REQUEST = httpx.Request("POST", PROVIDER_URL)


def make_settings(**fields: Any) -> Settings:
    """Isolated Settings (no .env file); overrides use field names."""
    return Settings(_env_file=None, **fields)


# --- construction / configuration --------------------------------------------


class TestConstruction:
    def test_missing_api_key_is_rejected(self):
        with pytest.raises(LLMNotConfiguredError):
            LLMService(api_key="")
        with pytest.raises(LLMNotConfiguredError):
            LLMService(api_key="   ")

    def test_blank_model_and_bad_knobs_are_rejected(self):
        with pytest.raises(LLMError):
            LLMService(api_key=API_KEY, model="  ")
        with pytest.raises(ValueError):
            LLMService(api_key=API_KEY, timeout_seconds=0)
        with pytest.raises(ValueError):
            LLMService(api_key=API_KEY, max_retries=11)

    def test_from_settings_requires_key(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(LLMNotConfiguredError, match="LLM_API_KEY"):
            LLMService.from_settings(make_settings())

    def test_from_settings_wires_configuration(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        settings = make_settings(
            llm_api_key="sk-test",
            llm_model="gpt-custom",
            llm_timeout_seconds=12,
            llm_max_retries=1,
        )
        assert settings.llm_configured is True
        # A real SDK client is built here; construction makes no network calls.
        service = LLMService.from_settings(settings)
        try:
            assert service.model == "gpt-custom"
        finally:
            service.close()

    def test_context_manager_closes(self):
        closed = []

        class Closable(SimpleNamespace):
            def close(self):
                closed.append(True)

        service = LLMService(
            api_key=API_KEY, client=Closable(chat=SimpleNamespace(completions=None))
        )
        with service:
            pass
        assert closed == [True]


# --- request/response contract ------------------------------------------------


class TestCompleteContract:
    def test_sends_system_and_user_messages(self):
        service, completions = stub_service(lambda _: fake_response())
        result = service.complete("Explain this financial signal.")

        call = completions.calls[0]
        assert call["model"] == "gpt-test"
        roles = [m["role"] for m in call["messages"]]
        assert roles == ["system", "user"]
        assert call["messages"][0]["content"] == DEFAULT_FINANCE_SYSTEM_PROMPT
        assert call["messages"][1]["content"] == "Explain this financial signal."
        assert isinstance(result, LLMCompletionResult)
        assert result.content == "all good"
        assert result.model == "gpt-test"
        assert result.finish_reason == "stop"

    def test_system_prompt_override(self):
        service, completions = stub_service(lambda _: fake_response())
        service.complete("hi", system_prompt="You are terse.")
        assert completions.calls[0]["messages"][0]["content"] == "You are terse."

    def test_default_prompt_forbids_llm_calculations(self):
        lowered = DEFAULT_FINANCE_SYSTEM_PROMPT.lower()
        assert "do not calculate" in lowered
        assert "never derive" in lowered
        assert "never invent" in lowered


class TestStructuredSignals:
    """The Phase 1 contract: structured signals in -> explanation out."""

    def test_signals_are_serialized_verbatim_and_deterministically(self):
        service, completions = stub_service(lambda _: fake_response())
        signals = {"revenue_growth": -20, "success_rate": 94}

        result = service.explain_signals(signals)

        user_content = completions.calls[0]["messages"][1]["content"]
        expected_json = json.dumps(signals, sort_keys=True, separators=(",", ": "))
        assert expected_json in user_content
        assert result.content == "all good"
        # Re-running produces a byte-identical prompt (deterministic framing).
        service.explain_signals({"success_rate": 94, "revenue_growth": -20})
        assert (
            completions.calls[0]["messages"][1]["content"]
            == completions.calls[1]["messages"][1]["content"]
        )

    def test_question_is_appended_without_touching_numbers(self):
        service, completions = stub_service(lambda _: fake_response())
        service.explain_signals(
            {"gross_revenue_minor": 600_000}, question="What changed?"
        )
        content = completions.calls[0]["messages"][1]["content"]
        assert '{"gross_revenue_minor": 600000}' in content
        assert content.endswith("Question: What changed?")

    def test_non_serializable_signals_are_rejected_cleanly(self):
        service, completions = stub_service(lambda _: fake_response())
        with pytest.raises(LLMError, match="JSON-serializable"):
            service.explain_signals({"bad": object()})
        assert completions.calls == []  # never reached the provider

    def test_reconciliation_signals_are_serialized_with_exact_values(self):
        """Reconciliation signals with all required keys are serialized
        verbatim — no rounding, no conversion, no omission."""
        service, completions = stub_service(lambda _: fake_response())
        recon_signals = {
            "data": {
                "total_records": 100,
                "matched_count": 70,
                "exception_count": 30,
                "unresolved_count": 2,
                "match_rate": 70.0,
            },
            "exception_summary": {
                "total_financial_exposure_minor": 16065382,
                "critical_count": 5,
                "high_count": 10,
                "medium_count": 10,
                "low_count": 5,
                "critical_exposure_minor": 8000000,
                "high_exposure_minor": 5000000,
                "medium_exposure_minor": 2000000,
                "low_exposure_minor": 1065382,
            },
        }
        service.explain_signals(recon_signals)

        user_content = completions.calls[0]["messages"][1]["content"]
        # Every number must appear exactly as supplied
        assert '"total_records": 100' in user_content
        assert '"matched_count": 70' in user_content
        assert '"exception_count": 30' in user_content
        assert '"unresolved_count": 2' in user_content
        assert '"match_rate": 70.0' in user_content
        assert '"total_financial_exposure_minor": 16065382' in user_content
        assert '"critical_count": 5' in user_content
        assert '"high_count": 10' in user_content
        assert '"medium_count": 10' in user_content
        assert '"low_count": 5' in user_content

    def test_explain_signals_never_modifies_signal_values(self):
        """The LLM service must pass signals through without any
        transformation — it is a framing layer only."""
        service, completions = stub_service(lambda _: fake_response())
        original = {
            "match_rate": 70.0,
            "total_financial_exposure_minor": 16065382,
            "critical_count": 5,
        }
        service.explain_signals(dict(original))

        user_content = completions.calls[0]["messages"][1]["content"]
        # The original values must appear verbatim
        for key, value in original.items():
            expected = f'"{key}": {value}'
            assert expected in user_content, (
                f"Signal key {key} with value {value} not found verbatim "
                f"in the LLM prompt"
            )

    def test_system_prompt_forbids_recalculating_financial_figures(self):
        """The default system prompt must explicitly forbid recalculating,
        converting, or reformatting financial numbers."""
        lowered = DEFAULT_FINANCE_SYSTEM_PROMPT.lower()
        assert "never derive" in lowered
        assert "recompute" in lowered
        assert "convert" in lowered
        assert "reformat" in lowered
        assert "never mention any number that is not present" in lowered


class TestJsonMode:
    def test_returns_parsed_object(self):
        payload = json.dumps({"summary": "ok", "severity": "low"})
        service, completions = stub_service(lambda _: fake_response(payload))
        parsed = service.complete_json("Give JSON")
        assert parsed == {"summary": "ok", "severity": "low"}
        assert completions.calls[0]["response_format"] == {"type": "json_object"}

    def test_non_json_content_raises_typed_error(self):
        service, _ = stub_service(lambda _: fake_response("not json at all"))
        with pytest.raises(LLMInvalidResponseError):
            service.complete_json("Give JSON")

    def test_non_object_json_raises_typed_error(self):
        service, _ = stub_service(lambda _: fake_response("[1, 2]"))
        with pytest.raises(LLMInvalidResponseError, match="not an object"):
            service.complete_json("Give JSON")


class TestResponseEdgeCases:
    def test_empty_content_raises_typed_error(self):
        service, _ = stub_service(lambda _: fake_response(content=""))
        with pytest.raises(LLMEmptyResponseError):
            service.complete("hello")

    def test_no_choices_raises_typed_error(self):
        malformed = iter([SimpleNamespace(model="gpt-test", choices=[], usage=None)])
        service, _ = stub_service(lambda _: next(malformed))
        with pytest.raises(LLMInvalidResponseError, match="no choices"):
            service.complete("hello")


# --- provider error translation -----------------------------------------------


class TestMaxTokensAndTruncation:
    """max_tokens wiring and the finish_reason='length' auto-retry."""

    def test_max_tokens_is_sent_to_provider(self):
        service, completions = stub_service(
            lambda _: fake_response(), max_tokens=2048
        )
        service.complete("hello")
        assert completions.calls[0]["max_tokens"] == 2048

    def test_no_max_tokens_omits_the_cap(self):
        service, completions = stub_service(
            lambda _: fake_response(), max_tokens=None
        )
        service.complete("hello")
        assert "max_tokens" not in completions.calls[0]

    def test_from_settings_wires_max_tokens(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        import openai as _openai

        class FakeOpenAI:
            def __init__(self, **kwargs):
                pass

            def close(self):
                pass

        monkeypatch.setattr(_openai, "OpenAI", FakeOpenAI)
        settings = make_settings(
            llm_api_key="sk-test",
            llm_model="gpt-custom",
            llm_max_tokens=2048,
        )
        service = LLMService.from_settings(settings)
        try:
            assert service._max_tokens == 2048
        finally:
            service.close()

    def test_truncated_flag_reflects_finish_reason(self):
        service, _ = stub_service(
            lambda _: fake_response(content="abc", finish_reason="length")
        )
        result = service.complete("write more")
        assert result.truncated is True

        service, _ = stub_service(
            lambda _: fake_response(content="abc", finish_reason="stop")
        )
        assert service.complete("done").truncated is False

    def test_truncation_retries_with_doubled_tokens(self):
        outcomes = iter(
            [
                fake_response(
                    content="partial answer...", finish_reason="length"
                ),
                fake_response(
                    content="the complete long answer exceeds the first budget",
                    finish_reason="stop",
                ),
            ]
        )
        service, completions = stub_service(
            lambda _: next(outcomes), max_tokens=1024
        )
        result = service.complete("Write a long explanation.")
        assert result.content == (
            "the complete long answer exceeds the first budget"
        )
        assert result.finish_reason == "stop"
        assert result.truncated is False
        assert completions.calls[0]["max_tokens"] == 1024
        assert completions.calls[1]["max_tokens"] == 2048
        assert len(completions.calls) == 2

    def test_truncation_persists_returns_longest_partial(self):
        outcomes = iter(
            [
                fake_response(content="cut off at first...", finish_reason="length"),
                fake_response(
                    content="cut off at second, longer...", finish_reason="length"
                ),
            ]
        )
        service, completions = stub_service(
            lambda _: next(outcomes), max_tokens=1024
        )
        result = service.complete("write even more")
        assert result.truncated is True
        assert result.content == "cut off at second, longer..."
        assert len(completions.calls) == 2

    def test_truncation_retry_failure_returns_original_partial(self):
        outcomes = iter(
            [
                fake_response(content="partial one...", finish_reason="length"),
                openai.InternalServerError(
                    "boom",
                    response=httpx.Response(500, request=REQUEST),
                    body=None,
                ),
            ]
        )
        service, completions = stub_service(
            lambda _: next(outcomes), max_tokens=1024
        )
        result = service.complete("hello")
        assert result.truncated is True
        assert result.content == "partial one..."
        assert len(completions.calls) == 2


class TestErrorTranslation:
    @pytest.mark.parametrize(
        ("provider_error", "expected_type"),
        [
            (status_error(openai.AuthenticationError, 401), LLMAuthenticationError),
            (status_error(openai.PermissionDeniedError, 403), LLMAuthenticationError),
            (status_error(openai.RateLimitError, 429), LLMRateLimitError),
            (status_error(openai.InternalServerError, 500), LLMServerError),
            (status_error(openai.BadRequestError, 400), LLMBadRequestError),
            (
                status_error(openai.UnprocessableEntityError, 422),
                LLMBadRequestError,
            ),
            (openai.APIConnectionError(request=REQUEST), LLMConnectionError),
            (openai.APITimeoutError(request=REQUEST), LLMConnectionError),
        ],
    )
    def test_sdk_errors_surface_as_typed_llm_errors(
        self, provider_error, expected_type
    ):
        service, _ = stub_service(lambda _: provider_error)
        with pytest.raises(expected_type):
            service.complete("hello")

    def test_error_messages_never_contain_the_key(self):
        service, _ = stub_service(
            lambda _: status_error(openai.AuthenticationError, 401)
        )
        with pytest.raises(LLMAuthenticationError) as excinfo:
            service.complete("hello")
        assert API_KEY not in str(excinfo.value)


# --- opt-in live integration ---------------------------------------------------


@pytest.mark.live_llm
def test_live_completion_when_enabled(monkeypatch):
    """Real API round-trip, strictly opt-in.

    Runs only when BOTH RECONAGENT_LLM_LIVE_TEST=1 and a configured
    LLM_API_KEY are present; otherwise it skips so `pytest -q` remains
    deterministic and free.
    """
    live_requested = os.environ.get("RECONAGENT_LLM_LIVE_TEST") == "1"
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = make_settings()
    if not live_requested or not settings.llm_configured:
        pytest.skip(
            "Live LLM check disabled (set RECONAGENT_LLM_LIVE_TEST=1 and "
            "LLM_API_KEY to enable)"
        )
    with LLMService.from_settings(settings) as service:
        result = service.explain_signals(
            {"success_rate": 100}, question="Reply with one short sentence."
        )
        assert result.content.strip()

"""Centralized LLM service — the only module that talks to a language model.

Architectural boundary (docs/AI_ARCHITECTURE.md — the short version):

- The Finance Intelligence Engine computes every financial number
  deterministically from stored records. Its output is a set of
  **structured financial signals**.
- This service receives those already-computed signals and returns
  human-language interpretation. It never queries the database, never
  reads raw ledger records, and never calculates a metric itself.
- The default system prompt encodes this boundary so the provider-side
  model is also instructed not to compute or invent financial figures.

Design conventions:

- One :class:`LLMService` owns one OpenAI SDK client (connection pooling)
  and is safe to reuse; it is a context manager for lifecycle control.
- All failures surface as typed :class:`LLMError` subclasses so callers
  can distinguish auth/rate-limit/network/server/contract problems
  without parsing provider payloads.
- Transient failures (429/5xx/network) are retried client-side by the
  SDK within the configured budget before an error surfaces here.
- Secrets are never logged; the API key is passed to the SDK and dropped.

Phase 1 scope: chat completions, JSON-mode completions, and the
signal-explanation helper. No agents, no tools, no LangGraph yet.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import openai

logger = logging.getLogger(__name__)

DEFAULT_FINANCE_SYSTEM_PROMPT = (
    "You are the AI layer of a finance controller application for an "
    "Indian payments business (normalized internal ledger data).\n"
    "You receive FINANCIAL SIGNALS that were already computed by a "
    "deterministic calculation engine from verified transaction records.\n"
    "Hard rules you must never break:\n"
    "1. You do not calculate financial metrics. Every number in the "
    "signals is authoritative; never derive, recompute, estimate, or "
    "correct any figure.\n"
    "2. You never invent data that is not present in the signals.\n"
    "3. You explain, interpret, and summarize what the signals mean for "
    "the business, in plain language.\n"
    "4. If the signals are insufficient to answer, say so explicitly "
    "instead of guessing."
)

# Compact, deterministic JSON serialization for structured signal blocks:
# stable key order keeps prompts byte-for-byte reproducible for the same
# input, which matters for prompt-level regression tests. No ``default``
# fallback on purpose: anything not JSON-native must be rejected loudly
# rather than silently stringified into the prompt.
_JSON_DUMPS_KWARGS = {"sort_keys": True, "separators": (",", ": ")}


class LLMError(Exception):
    """Base class for all LLM integration failures."""


class LLMNotConfiguredError(LLMError):
    """No API key is configured; the service cannot be built."""


class LLMAuthenticationError(LLMError):
    """HTTP 401/403 — the configured key is invalid or unauthorized."""


class LLMRateLimitError(LLMError):
    """HTTP 429 persisted after exhausting bounded client retries."""


class LLMServerError(LLMError):
    """HTTP 5xx persisted after exhausting bounded client retries."""


class LLMConnectionError(LLMError):
    """Network failure or timeout while reaching the LLM provider."""


class LLMBadRequestError(LLMError):
    """HTTP 400/422 — the request was rejected as malformed/unsupported."""


class LLMInvalidResponseError(LLMError):
    """The provider answered but the body was not in the expected shape."""


class LLMEmptyResponseError(LLMInvalidResponseError):
    """The provider returned no textual content for a completion."""


@dataclass(frozen=True)
class LLMUsage:
    """Token accounting reported by the provider (all optional)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMCompletionResult:
    """One completed LLM call, provider-shaped but provider-neutral."""

    content: str
    model: str
    finish_reason: str | None = None
    usage: LLMUsage | None = None


def _translate_provider_error(exc: Exception) -> LLMError:
    """Map an OpenAI SDK exception onto the typed LLM error hierarchy.

    Messages come from the SDK exception text only — no headers, bodies,
    or credentials are embedded.
    """
    if isinstance(exc, openai.AuthenticationError) or isinstance(
        exc, openai.PermissionDeniedError
    ):
        return LLMAuthenticationError(str(exc))
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(str(exc))
    if isinstance(exc, openai.APITimeoutError):
        return LLMConnectionError(f"LLM request timed out: {exc}")
    if isinstance(exc, openai.APIConnectionError):
        return LLMConnectionError(str(exc))
    if isinstance(exc, openai.InternalServerError):
        return LLMServerError(str(exc))
    if isinstance(exc, openai.UnprocessableEntityError) or isinstance(
        exc, openai.BadRequestError
    ):
        return LLMBadRequestError(str(exc))
    return LLMError(str(exc))


def _usage_from(payload: Any) -> LLMUsage | None:
    """Defensively read usage fields off a provider response."""
    if payload is None:
        return None

    def _int(name: str) -> int | None:
        value = getattr(payload, name, None)
        return int(value) if value is not None else None

    return LLMUsage(
        prompt_tokens=_int("prompt_tokens"),
        completion_tokens=_int("completion_tokens"),
        total_tokens=_int("total_tokens"),
    )


class LLMService:
    """Centralized gateway to the configured LLM provider."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        system_prompt: str = DEFAULT_FINANCE_SYSTEM_PROMPT,
        client: openai.OpenAI | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise LLMNotConfiguredError(
                "An API key is required to build the LLM service"
            )
        if not model or not model.strip():
            raise LLMError("A model name is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10")
        self._model = model
        self._system_prompt = system_prompt
        self._client = client or openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> "LLMService":
        """Build a service from application settings; fail clearly if unset."""
        api_key = (
            settings.llm_api_key.get_secret_value()
            if settings.llm_api_key is not None
            else None
        )
        if not settings.llm_configured or not api_key:
            raise LLMNotConfiguredError(
                "LLM integration is not configured: set LLM_API_KEY"
            )
        return cls(
            api_key=api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            **overrides,
        )

    @property
    def model(self) -> str:
        return self._model

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LLMService":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- public API -------------------------------------------------------

    def complete(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
    ) -> LLMCompletionResult:
        """Send one user message under the system prompt; return the reply."""
        return self._create(
            system=system_prompt or self._system_prompt, user=message
        )

    def complete_json(
        self, message: str, *, system_prompt: str | None = None
    ) -> dict[str, Any]:
        """Like :meth:`complete`, but the reply must be a JSON object.

        Uses the provider's JSON response mode; malformed output raises
        :class:`LLMInvalidResponseError` instead of leaking raw payloads.
        """
        result = self._create(
            system=system_prompt or self._system_prompt,
            user=message,
            extra={"response_format": {"type": "json_object"}},
        )
        try:
            parsed = json.loads(result.content)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError(
                f"LLM returned non-JSON content: {exc.msg}"
            ) from exc
        if not isinstance(parsed, dict):
            raise LLMInvalidResponseError(
                "LLM returned JSON that is not an object"
            )
        return parsed

    def explain_signals(
        self,
        signals: Mapping[str, Any],
        *,
        question: str | None = None,
        system_prompt: str | None = None,
    ) -> LLMCompletionResult:
        """Interpret pre-computed structured financial signals.

        ``signals`` must already contain every number to discuss — they are
        serialized verbatim into the prompt. This method adds no values of
        its own and performs no arithmetic; it is pure framing.
        """
        try:
            signals_json = json.dumps(dict(signals), **_JSON_DUMPS_KWARGS)
        except (TypeError, ValueError) as exc:
            raise LLMError(
                f"signals must be JSON-serializable: {exc}"
            ) from exc

        message = (
            "Interpret these pre-computed financial signals:\n"
            f"{signals_json}"
        )
        if question:
            message = f"{message}\n\nQuestion: {question}"
        return self.complete(message, system_prompt=system_prompt)

    # --- internals ----------------------------------------------------------

    def _create(
        self, *, system: str, user: str, extra: dict[str, Any] | None = None
    ) -> LLMCompletionResult:
        """One chat-completions call with typed failure translation."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if extra:
            kwargs.update(extra)
        logger.debug(
            "LLM request issued (model=%s, bytes=%d)",
            self._model,
            len(user.encode("utf-8")),
        )
        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            raise _translate_provider_error(exc) from exc

        choices = list(getattr(response, "choices", None) or [])
        if not choices:
            raise LLMInvalidResponseError("LLM response contained no choices")
        message_payload = getattr(choices[0], "message", None)
        content = getattr(message_payload, "content", None)
        if not content or not str(content).strip():
            raise LLMEmptyResponseError(
                "LLM returned no textual content"
            )
        finish_reason = getattr(choices[0], "finish_reason", None)
        return LLMCompletionResult(
            content=str(content),
            model=str(getattr(response, "model", self._model)),
            finish_reason=(
                str(finish_reason) if finish_reason is not None else None
            ),
            usage=_usage_from(getattr(response, "usage", None)),
        )


__all__ = [
    "DEFAULT_FINANCE_SYSTEM_PROMPT",
    "LLMAuthenticationError",
    "LLMBadRequestError",
    "LLMCompletionResult",
    "LLMConnectionError",
    "LLMEmptyResponseError",
    "LLMError",
    "LLMInvalidResponseError",
    "LLMNotConfiguredError",
    "LLMRateLimitError",
    "LLMServerError",
    "LLMService",
    "LLMUsage",
]

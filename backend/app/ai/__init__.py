"""AI layer of the ReconAgent backend.

Architectural boundary (docs/AI_ARCHITECTURE.md):

- The Finance Intelligence Engine (``app.services.metrics``) is the
  deterministic source of financial truth: it computes every metric from
  stored records, with no LLM anywhere in that path.
- This package is the ONLY place the application talks to a language
  model. Everything else must go through :class:`app.ai.llm.LLMService`.
- The ``app.ai.tools`` subpackage is the controlled adapter that exposes
  Finance Intelligence Engine results to the AI orchestration layer (see
  docs/AI_TOOLS.md). Tools compute nothing themselves and call no LLM.
- The ``app.ai.graph`` subpackage is the LangGraph orchestration over
  those tools (see docs/LANGGRAPH_ARCHITECTURE.md): it plans tool usage,
  executes the fixed allowlist, and grounds LLM interpretation in the
  retrieved signals.

Implemented scope: the centralized LLM service, its configuration, the
deterministic Finance Tools layer, and the read-only LangGraph finance
agent. No recommendations, no chat API endpoint, no frontend yet.
"""

from .llm import (
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
    LLMUsage,
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

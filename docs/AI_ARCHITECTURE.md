# AI Architecture — ReconAgent Finance Controller

**Status:** Phase 1 (AI Foundation + LLM Integration), Phase 2 (Finance
Tools adapter, `app.ai.tools` + `docs/AI_TOOLS.md`), Phase 3
(LangGraph orchestration, `app.ai.graph` + `docs/LANGGRAPH_ARCHITECTURE.md`),
Phase 4 (chat API endpoint, `POST /api/v1/ai/chat` via `app.api.ai`),
and Track 04 (deterministic reconciliation loop,
`docs/RECONCILIATION_ARCHITECTURE.md`) — implemented.
Recommendations remain a future phase.
**Related documents:**

- `docs/FINANCE_METRICS_SPECIFICATION.md` — source of truth for every
  deterministic financial metric
- `docs/ReconAgent_Master_Spec.md`, `docs/TECH_STACK_AND_ARCHITECTURE.md`
  — overall system design

---

## 1. Current architecture

```
Razorpay API
      │  (sync, read-only)
      ▼
PostgreSQL database            app.db.*
      │
      ▼
Finance Intelligence Engine    app.services.metrics.FinanceService
      │  deterministic calculations only — no LLM anywhere
      ▼
Structured Financial Signals   app.schemas.finance.* responses
      │
      ▼
AI / LLM Layer                 app.ai.llm.LLMService        ← Phase 1
      │  interpretation and explanation ONLY
      ▼
Business Explanation
```

The two halves of this pipeline are separated by a hard architectural
boundary:

- **Finance Intelligence Engine** = deterministic source of financial truth.
- **AI layer** = interpretation of already-computed signals.

Nothing in between: no component may bypass `FinanceService` to hand raw
records to the LLM, and no LLM output may be treated as a financial fact.

## 2. Finance Intelligence Engine responsibility

Implemented in `backend/app/services/metrics.py`, specified by
`docs/FINANCE_METRICS_SPECIFICATION.md`. It owns, exclusively:

- transaction volume, successful/failed/in-progress classification
  (spec §2.0–§2.5)
- gross revenue, refund amount/count/rate, fee/tax totals, and the three
  separately-labeled net-revenue variants (spec Part 3–4)
- settlement amounts/fees/tax and retrospective reconciliation inputs
  (spec Part 5)
- period-over-period trends with explicit zero-denominator semantics
  (spec Part 6)

Its guarantees (spec Part 11): exact minor-unit integer arithmetic,
single-currency aggregation, dedupe-by-id, deterministic bit-for-bit
output, division-by-zero never occurring, and **no LLM influence on any
number**.

Its outputs (`PaymentPerformanceResponse`, `RevenueMetricsResponse`,
`TrendResponse`, …) are exactly the "structured financial signals" the AI
layer consumes. The engine is unchanged by Phase 1; its full test suite
(378 passed, 4 skipped at baseline) is the regression gate.

The same boundary holds for exception **triage**: severity, priority and
recommended actions on every reconciliation result are deterministic
policy decisions computed by `backend/app/services/reconciliation_policy.py`
before the model runs (see Reconciliation Architecture §5.1). The LLM may
describe these annotations conversationally, but it never assigns,
overrides or computes them.

## 3. LLM responsibility

Implemented in `backend/app/ai/llm.py` (`LLMService`). It receives
structured signals and produces human-language interpretation:
explanations, summaries, and (in later phases) recommendations.

The LLM is permitted to:

- explain what pre-computed signal values mean for the business
- compare/contrast values that are both present in the input
- highlight what deserves human attention

The LLM must NOT — and this is enforced architecturally plus in the
default system prompt:

- calculate gross revenue from raw payments
- calculate success rate, failure rate, or refund rate
- calculate settlement differences or any other metric
- invent, estimate, "correct", or extrapolate missing financial values
- query the database directly or access raw Razorpay records
- replace `FinanceService` in any data path

If asked for numbers it was not given, the correct behavior is to say the
information is insufficient.

## 4. Why financial calculations remain deterministic

1. **Auditability** — every rupee figure must trace back to stored
   records via a documented formula that an auditor can re-run.
2. **Correctness under non-determinism** — LLMs can hallucinate;
   sampling temperature means identical inputs can yield different
   outputs. Financial arithmetic must be reproducible bit-for-bit.
3. **Testability** — the engine's behavior is pinned by exact-value unit
   tests against the metrics specification; free-form model output cannot
   carry such a contract.
4. **Failure isolation** — when the provider is down, rate-limited, or
   misconfigured, deterministic reporting continues unaffected; only the
   interpretive layer degrades.

This mirrors the master spec's principle: *"AI-assisted investigation,
not AI-authoritative computation."*

## 5. Current LLM service design

`app.ai.llm.LLMService` — the single gateway to the provider. All other
modules must go through it; nothing else imports the SDK.

- **Provider:** OpenAI-compatible chat-completions API via the official
  `openai` SDK. `LLM_BASE_URL` repoints the client at any compatible
  endpoint without code changes.
- **Construction:** `LLMService(api_key=…, …)` or
  `LLMService.from_settings(settings)` (raises `LLMNotConfiguredError`
  when no key is configured). Context-manager lifecycle, one pooled SDK
  client per service.
- **Capabilities (Phase 1 scope):**
  - `complete(message)` — plain text completion under the system prompt
    (override allowed per call)
  - `explain_signals(signals, question=…)` — serializes structured
    financial signals deterministically (`sort_keys`, compact separators)
    into the prompt and requests interpretation; adds nothing, computes
    nothing
  - `complete_json(message)` — JSON-object response mode with defensive
    parsing (`LLMInvalidResponseError` on malformed/non-object output)
- **Default system prompt** (`DEFAULT_FINANCE_SYSTEM_PROMPT`) encodes the
  boundary rules above at the provider side as defense in depth.
- **Results:** `LLMCompletionResult(content, model, finish_reason, usage)`
  — typed and provider-neutral.

### Error handling

All failures surface as typed exceptions derived from `LLMError`, with
messages built only from SDK exception text — never headers, raw bodies,
or credentials:

| Failure | Exception |
|---|---|
| No API key configured | `LLMNotConfiguredError` |
| HTTP 401/403 | `LLMAuthenticationError` |
| HTTP 429 after bounded retries | `LLMRateLimitError` |
| HTTP 5xx after bounded retries | `LLMServerError` |
| Network error / timeout | `LLMConnectionError` |
| HTTP 400/422 | `LLMBadRequestError` |
| Unexpected body shape / empty content | `LLMInvalidResponseError` / `LLMEmptyResponseError` |
| Non-JSON despite JSON mode | `LLMInvalidResponseError` |

Transient failures (429/5xx/network) are retried client-side by the SDK
within the configured budget (`LLM_MAX_RETRIES`) before surfacing.

## 6. Configuration and secrets

All configuration lives in the existing pydantic-settings system
(`app/core/config.py`) and is environment-driven — no secrets in code:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_API_KEY` | *(unset)* | Provider key; required for the AI layer, mandatory in production |
| `LLM_MODEL` | `gpt-4o-mini` | Chat model served through the API |
| `LLM_BASE_URL` | *(unset)* | Optional HTTPS override for OpenAI-compatible endpoints |
| `LLM_TIMEOUT_SECONDS` | `30` | Per-request timeout (must be > 0) |
| `LLM_MAX_RETRIES` | `2` | Client-side transient-failure retry budget (0–10) |

Validation: blank `LLM_API_KEY` is treated as unset (a half-filled `.env`
cannot masquerade as configured); blank `LLM_MODEL` falls back to the
default; `LLM_BASE_URL` must be absolute HTTPS; timeout/retries are range
checked. The key is held as `SecretStr` — masked in `repr`, never logged,
never embedded in exception messages. See `backend/.env.example` (and the
root union template) for the documented variables; `.env` itself is
git-ignored and must never be committed.

## 7. Testing strategy

Two strictly separated tiers (`backend/tests/test_ai_llm.py`):

1. **Deterministic unit tests (default suite).** A stub completions
   client records request kwargs and replays scripted responses/errors —
   no network, no key needed. Covers: construction guards, settings
   wiring, system/user message shape, boundary prompt content,
   deterministic signal serialization, JSON mode (happy/malformed/
   non-object), empty-content and missing-choices contract drift, full
   error-translation matrix, and API-key leakage prevention. Settings
   validation lives in `tests/test_config.py::TestLLMConfiguration`.
2. **Opt-in live test.** One integration test performs a real round-trip
   but runs only when BOTH `RECONAGENT_LLM_LIVE_TEST=1` AND a configured
   `LLM_API_KEY` exist; otherwise it skips cleanly. Plain `pytest -q`
   therefore stays deterministic and free. Registered marker:
   `live_llm`.

## 8. Future architecture (NOT yet implemented)

Planned evolution — the first two boxes exist in the codebase today
(`app.ai.tools` per `docs/AI_TOOLS.md`; `app.ai.graph` per
`docs/LANGGRAPH_ARCHITECTURE.md`):

```
Finance Tools (deterministic queries over FinanceService outputs)   ← implemented
      ↓
LangGraph (stateful orchestration: plan → execute → interpret)      ← implemented
      ↓
LLM (reasoning constrained to tool-retrieved evidence)
      ↓
Financial Analysis (root causes, narratives)                        ← future depth
      ↓
Recommendations (human-in-the-loop; never auto-applied)             ← future
```

- **Finance Tools** (implemented) expose narrow, read-only slices of
  `FinanceService` results so the agent can *retrieve* evidence rather
  than compute it. They call no LLM and touch no database directly.
- **LangGraph** (implemented, read-only single-pass graph) orchestrates
  tool selection and execution with explicit state. Multi-step
  investigation loops remain future work.
- **Chat API** (implemented, `app.api.ai`) wraps the agent as
  `POST /api/v1/ai/chat`: validated question in, structured facts +
  LLM `answer` out. A missing `LLM_API_KEY` or database answers a clean
  503; graph-level degradation is reported via the response `status`
  (`completed` / `partial` / `failed`), never an HTTP 5xx.
- **Reconciliation loop** (implemented — Track 04) adds a deterministic
  finance-ops engine behind the same walls: the
  `reconcile_transactions` tool runs fixed matching rules over a seeded
  synthetic batch (no database), is plannable in chat via keyword or LLM
  selection, and is exposed as `POST /api/v1/ai/reconcile`. The LLM may
  only narrate the returned report; measured accuracy lives solely in
  the evaluation harness (`scripts/reconcile_benchmark.py`). Full design:
  `docs/RECONCILIATION_ARCHITECTURE.md`.
- **Recommendations** will remain suggestions requiring human approval;
  per the master spec, the LLM never writes financial records.

Deliberately NOT implemented yet: recommendation engine, anomaly
detection, forecasting, frontend AI UI, and conversation memory.

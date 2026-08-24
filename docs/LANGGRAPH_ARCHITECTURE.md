# LangGraph Finance Intelligence Orchestration

Status: **implemented** (`backend/app/ai/graph/`) — read-only finance Q&A.

```
                    User Question
                         ↓
                  LangGraph State
                         ↓
                  Intent / Planning          (LLM proposes; code validates)
                         ↓
              Registered Finance Tools       (app.ai.tools — fixed allowlist)
                ↙       ↓       ↘
           Revenue   Payments   Refunds  … Settlements / Trends / Summary
                ↘       ↓       ↙
              Financial Signals             (facts — verbatim envelopes)
                       ↓
                 LLMService                (interpretation only)
                       ↓
              Grounded Explanation           (kept separate from facts)
```

Layer responsibilities (unchanged from earlier phases):

| Layer | Responsibility |
|---|---|
| Finance Intelligence Engine | CALCULATE — deterministic metrics |
| Finance Tools | EXPOSE — validated access to engine results |
| **LangGraph (this phase)** | ORCHESTRATE — plan → execute → interpret |
| LLM | INTERPRET — explain retrieved signals |

The LLM must never become the calculation engine. Every number in an
answer traces to a tool envelope produced by `FinanceService`.

## 1. Why LangGraph

Even a single finance question is a *workflow*, not one prompt: pick
relevant tools, execute them with a live database session, then ground an
LLM answer in what came back. LangGraph models this as explicit state +
nodes so every transition is inspectable and testable, and later phases
(branching, richer agents) extend rather than replace it.

## 2. Graph structure (`graph.py`)

Linear and deterministic: `START → plan → execute_tools → interpret → END`,
with one conditional edge — if execution halts fatally (e.g. no database
session), `execute_tools` sets `halted` and the graph goes straight to
`END`; the LLM is never asked to explain data that was not retrieved.

## 3. Graph state (`state.py`)

`FinanceGraphState` is a `TypedDict` of JSON-native channels only:
`question`, `plan` (list of `{tool, arguments}`), `selection_source`,
`tool_results`, `tool_errors`, `interpretation`, `status`
(`completed`/`partial`/`failed`), `errors`, `halted`.

Never stored in state: DB sessions, SQLAlchemy objects, credentials, API
keys, environment values. The request-scoped session reaches nodes via
`RunnableConfig["configurable"][DB_SESSION_CONFIG_KEY]` — injected per
run, exactly like FastAPI dependency injection.

Facts vs interpretation are separate channels by construction:
`tool_results` holds verbatim tool envelopes; `interpretation` holds the
LLM's words. They are never merged.

## 4. Nodes (`nodes.py`)

### `plan_node`
- Rejects empty questions immediately (`status="failed"`, no LLM call).
- Asks the configured `LLMService.complete_json()` to choose tools using
  `PLANNER_SYSTEM_PROMPT` plus a catalog generated live from the tool
  registry (`tool_catalog()`: names, descriptions, argument JSON schemas).
- **Sanitizes everything the LLM returns**: entries must name a registered
  tool (checked against `FINANCE_TOOLS`), arguments must be a dict,
  duplicates collapse, count capped at `MAX_TOOLS_PER_QUESTION = 4`.
  Unknown or malformed proposals are dropped and logged — never executed.
- On any planner `LLMError`, or when the proposal sanitizes to nothing,
  falls back to `_keyword_plan`: a deterministic keyword→tool map whose
  plans are always executable (the trend entry supplies its required
  `metric`). No keyword match ⇒ `financial_summary` (safe default).
  Reconciliation questions (`"reconcil"` substring) are routed first and
  **alone**: `reconcile_transactions` is self-contained, so no dashboard
  tools are attached to the same run.

### `execute_tools_node`
- Runs each planned call through the existing `get_finance_tool(name)
  .run(session, arguments)` — tools are reused, not rewritten.
- Halts before any execution only when the session is missing **and at
  least one selected tool requires one** (`requires_session` on the tool
  class; every database-backed tool defaults to `True`). Sessionless
  tools (e.g. `reconcile_transactions`, synthetic-dataset reconciliation)
  run with no database, alone or alongside DB tools when a session
  exists.
- Per-tool failures (`InvalidToolInputError`, `FinanceEngineError`, …) are
  captured into `tool_errors` with already-sanitized messages; other tools
  still run. Unexpected exceptions are logged server-side and reduced to a
  generic message (no SQL, DSNs, or stack traces).
- Successes land in `tool_results` keyed by tool name.

### `interpret_node`
- Builds signals `{"data": <envelopes>}` (+ `"errors"` block when tools
  failed) and calls `LLMService.explain_signals(...)` under
  `INTERPRETATION_SYSTEM_PROMPT`.
- Status semantics: `completed` (data + interpretation, no errors),
  `partial` (some failures, or interpretation unavailable while data was
  retrieved), `failed` (nothing retrieved).

## 5. Tool registration

Tools are the existing singletons in `app.ai.tools`. Nothing about them
changed for this phase. The planner catalog is derived from
`ALL_FINANCE_TOOLS` at call time, so adding a future tool automatically
makes it plannable — and automatically allowlisted, since selection is
validated against the same registry.

## 6. Financial safety rules

`INTERPRETATION_SYSTEM_PROMPT` extends `DEFAULT_FINANCE_SYSTEM_PROMPT`
with grounding language: separate FACTS from INTERPRETATION from POSSIBLE
EXPLANATIONS; prefer wording like *"The data shows…"*, *"A possible
explanation is…"*, *"The available data does not establish…"*; acknowledge
failed retrievals; never introduce values not present in the signals.

## 7. Public service API (`service.py`)

```python
from app.ai.graph import FinanceIntelligenceAgent

agent = FinanceIntelligenceAgent.from_settings(settings)   # once at startup
result = agent.run(db_session, "What was my revenue this month?")

result.status            # "completed"
result.selected_tools    # ["revenue"]
result.tool_results      # {"revenue": {"tool": "revenue", ...}}  <- FACTS
result.tool_errors       # {}
result.interpretation    # "The data shows ..."                  <- WORDS
result.errors            # []
```

`FinanceAgentResult` is a Pydantic model (JSON round-trip tested). The
chat API endpoint (`POST /api/v1/ai/chat`, `app.api.ai`) wraps exactly
this call: it validates the question, supplies the request-scoped DB
session, projects `FinanceAgentResult` onto the public response schema
(`interpretation` is exposed as `answer`; facts stay in `tool_results`
and `financial_signals`), and maps an unconfigured LLM key to 503.
Graph-level degradation (`partial` / `failed`) stays a structured 200
response — it is an outcome of the run, not an HTTP failure.

## 8. Error handling summary

| Failure | Behavior |
|---|---|
| Empty question | immediate controlled `failed`, nothing called |
| Planner LLM error | logged; deterministic keyword fallback |
| Unregistered/malformed proposal | dropped; remaining valid selections kept |
| Invalid tool arguments | per-tool sanitized error; siblings unaffected |
| Tool engine failure | per-tool error; interpretation sees honest gaps |
| Interpretation LLM error | raw data returned unexplained; `partial` |
| Missing session | halts before interpretation when a selected tool needs the database (`failed`); sessionless-only plans (reconciliation) still run |
| Secrets/internal details | never in messages, state, logs, or results |

## 9. Security boundaries

The LLM can only (a) propose tool names — filtered to the registry — and
(b) phrase text about returned JSON. It cannot execute SQL, reach
repositories or sessions, read environment variables, invoke arbitrary
Python, or touch external payment-provider APIs: those capabilities simply
do not exist in
the graph modules (asserted by source-scan tests) and the session flows
through config that only nodes read. Logging records lifecycle events,
selected tool names, durations, and status codes — never secrets or raw
financial payloads.

## 10. Example flows

**Single-tool:** "What is my revenue this month?" → plan(`revenue`,
`{period: this_month}`) → revenue envelope → grounded answer.

**Multi-tool:** "Why did revenue decrease?" → plan(`revenue`, `trends`,
`payment_performance`) → three envelopes (+ errors block for any gap) →
interpretation distinguishing observed changes from possible causes.

**Overview:** "Give me my financial summary." → plan(`financial_summary`)
→ multi-currency envelope → plain-language explanation.

**Degraded:** planner down → keyword fallback; interpretation down →
structured data returned with `status=partial`.

## 11. Future work (NOT implemented)

Frontend UI, conversation memory across turns, recommendation engine,
autonomous actions — deliberately out of scope; this agent is strictly
read-only orchestration.

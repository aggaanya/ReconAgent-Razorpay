# Finance Tools Layer (AI)

Status: **implemented** — the adapter between the Finance Intelligence
Engine and the AI orchestration layer.

```
Finance Intelligence Engine        (app.services.metrics — deterministic truth)
          ↓
Structured Financial Signals       (Pydantic read models, app.schemas.finance)
          ↓
Finance Tools                      (app.ai.tools — THIS layer, adapter only)
          ↓
LangGraph                          (implemented — app.ai.graph,
                                    docs/LANGGRAPH_ARCHITECTURE.md)
          ↓
LLM (orchestration)                (interpretation via app.ai.llm.LLMService)
```

> **Finance Tools do not perform financial calculations.** They expose the
> existing deterministic Finance Intelligence results to the AI
> orchestration layer. Every metric definition lives in
> `docs/FINANCE_METRICS_SPECIFICATION.md` and is computed exclusively by
> `FinanceService` (`backend/app/services/metrics.py`).

## 1. Why this layer exists

The future LangGraph/agent layer must never touch SQLAlchemy sessions,
repositories, SQL, or Razorpay APIs directly, and the LLM must never be
trusted to compute financial values. Finance Tools are the narrow,
validated, JSON-only seam in between (the graph reaches them exclusively
through `get_finance_tool(name).run(session, arguments)`):

- one tool = one `FinanceService` capability;
- typed Pydantic inputs validated at the boundary (so LLM-produced
  arguments cannot inject anything unexpected);
- outputs are plain JSON-native dicts, verbatim echoes of engine
  responses — safe to feed into `LLMService.explain_signals()` or a
  LangGraph state later.

## 2. Available tools

Registry: `ALL_FINANCE_TOOLS`, lookup via `get_finance_tool(name)`
(raises `UnknownToolError` for unknown names).

| Tool name | Class | FinanceService method called | Returns |
|---|---|---|---|
| `revenue` | `RevenueTool` | `FinanceService.revenue(session, start=, end=, currency=)` | gross revenue, fee/tax sums, 3 net variants, refund block |
| `payment_performance` | `PaymentPerformanceTool` | `FinanceService.payment_performance(session, start=, end=, currency=)` | volume, success/fail/in-progress counts, rates |
| `refunds` | `RefundTool` | `FinanceService.revenue(...)` (same call — refund metrics are defined over the same window) | processed-only refund count/amount/rate + gross-revenue denominator |
| `settlements` | `SettlementTool` | `FinanceService.summary(session, start_date=, end_date=, currency=)` | settlement amount/fees/tax per currency (projected fields) |
| `trends` | `TrendTool` | `FinanceService.trend(session, metric=, granularity=, now=, currency=)` | current vs previous windows, absolute/percentage change, change type |
| `financial_summary` | `FinancialSummaryTool` | `FinanceService.summary(session, start_date=, end_date=, currency=, status=, method=)` | full multi-currency summary |
| `reconcile_transactions` | `ReconcileTransactionsTool` | none — deterministic reconciliation engine (`app.services.reconciliation`) over a seeded synthetic batch; **no database** (`requires_session = False`) | summary metrics (records, matched, exceptions, match rate, throughput), full typed exception list, per-record results; `accuracy` is always `null` in serving (ground truth lives only in the evaluation harness — see `docs/RECONCILIATION_ARCHITECTURE.md`) |

Named periods (`today`, `yesterday`, `this_week`, `previous_week`,
`this_month`, `previous_month`) are resolved with the existing
`app.core.periods.named_period` on an IST clock (spec §6.2) — identical to
the finance API. Trend metric names and granularities are `Literal`s that
mirror `TREND_METRIC_EXTRACTORS` / `TREND_GRANULARITIES`; drift tests fail
if they ever diverge.

## 3. Input contracts

Each tool accepts its input model instance **or a plain dict** (coerced
once at the boundary; anything else → `InvalidToolInputError`). All models
forbid extra fields.

| Tool | Input model | Fields |
|---|---|---|
| `revenue` | `RevenueToolInput` | `period: PeriodName = "today"`, `currency: str = "INR"` |
| `payment_performance` | `PaymentPerformanceToolInput` | same shape as revenue |
| `refunds` | `RefundToolInput` | same shape as revenue |
| `settlements` | `SettlementToolInput` | `start_date/end_date: date?` (inclusive UTC days), `currency?: str` filter |
| `trends` | `TrendToolInput` | `metric: TrendMetricName`, `granularity: "day"\|"week"\|"month" = "day"`, `currency = "INR"` |
| `financial_summary` | `FinancialSummaryToolInput` | settlements fields + `status?: str`, `method?: str` |
| `reconcile_transactions` | `ReconcileToolInput` | `source: "synthetic" = "synthetic"`, `seed: int ≥ 0 = 42`, `size: int 50–5000 = 100` — dataset selection only, never record contents |

Currency values are normalized (strip + upper) and length-checked exactly
like the finance API's single-currency guard; `start > end` raises at
validation time.

## 4. Output contract

Every tool returns one JSON-native dict:

```jsonc
{
  "tool": "revenue",                       // stable registry name
  "period": {"start": "...", "end": "...", "timezone": "Asia/Kolkata"},
                                           // date-window tools; summary-style
                                           // tools use "window" (+ "filters")
  "data": { /* engine response, model_dump(mode="json"), fields unchanged */ }
}
```

- Engine responses are serialized with Pydantic `model_dump(mode="json")`,
  then hard-checked with `json.dumps` inside `run()` — non-serializable
  output is impossible by construction.
- Field names and semantics are preserved exactly (minor-unit integers,
  `null` rates where undefined per spec Part 6.4). The `refunds` tool adds
  nothing but field selection plus the engine's own `gross_revenue_minor`
  denominator.
- No SQLAlchemy objects, sessions, repositories, or datetimes-as-objects
  can ever escape this layer.

Example:

```python
from sqlalchemy.orm import Session
from app.ai.tools import get_finance_tool

tool = get_finance_tool("revenue")
result: dict = tool.run(db_session, {"period": "this_month", "currency": "INR"})
# -> {"tool": "revenue", "period": {...}, "data": {"gross_revenue_minor": ..., ...}}
```

## 5. Error handling

Typed hierarchy in `app.ai.tools.base` (each error carries `code`, and
`tool` when applicable):

| Error | `code` | Raised when |
|---|---|---|
| `InvalidToolInputError` | `invalid_input` | bad period/metric/granularity/currency/date order, wrong payload type, missing session |
| `UnknownToolError` | `unknown_tool` | registry lookup miss |
| `FinanceEngineError` | `engine_error` | any `FinanceService` failure, incl. unconfigured database |

Sanitization rules (enforced centrally in `FinanceTool.run`):

- unexpected engine exceptions are logged server-side (with traceback) and
  re-raised as `FinanceEngineError`; the public message contains only the
  tool name and exception *class* — never SQL text, DSNs, credentials, or
  stack traces (original exception stays chained as `__cause__`);
- `DatabaseNotConfiguredError` passes through its deliberate public message;
- empty datasets are not errors — the engine degrades to zero-filled,
  well-defined payloads which the tools echo unchanged.

## 6. Security boundaries

Tools **never**: open/store database sessions (one is injected per call;
`reconcile_transactions` is the deliberate exception — it declares
`requires_session = False` because it reconciles a seeded synthetic batch,
never live records), touch repositories or SQL, call Razorpay APIs, call
the LLM, expose
connection strings/API keys, or accept free-form queries. The AI layer can
only obtain financial data through these fixed contracts.

## 7. LangGraph integration (implemented)

The design anticipated LangGraph/LangChain registration — stable `name`,
`description`, `input_model` (JSON-schema-able via Pydantic), dict-coercing
`run()` — and it is wired exactly that way: the graph's planner builds its
tool catalog from this registry, validates every LLM-proposed selection
against it, then executes
`LangGraph node -> tool.run(session, llm_arguments) -> envelope ->
LLMService.explain_signals()`. See `docs/LANGGRAPH_ARCHITECTURE.md`.
Still NOT implemented: frontend UI, conversation memory, recommendations.

# ReconAgent

**AI Finance Controller — Multi-Source Reconciliation & Exception Intelligence**

ReconAgent is an enterprise-grade, AI-assisted financial reconciliation platform. It ingests three data sources — payment/settlement records, bank statements, and the internal ledger — matches them with **deterministic, auditable code**, hands only the mismatches ("exceptions") to an **AI agent** that investigates, explains, and recommends resolutions, and requires an explicit **human decision** before anything is marked resolved. Every step is written to an audit trail.

The authoritative product/technical blueprint is [`docs/ReconAgent_Master_Spec.md`](docs/ReconAgent_Master_Spec.md).

---

## Current status

**Implemented and verified on this machine:**

| Module | Status |
|---|---|
| Backend FastAPI service (`/health`, `/readiness`), PostgreSQL schema + Alembic migrations, Razorpay integration & sync | ✅ implemented |
| Finance metrics + REST APIs (`/api/v1/finance/*`) | ✅ implemented |
| Deterministic reconciliation engine (Track 04) — fixed rules R0–R9, refund integrity, fee/tax expected settlement, typed exception reasons, compound exceptions | ✅ implemented (`backend/app/services/reconciliation.py`) |
| Synthetic 100-record dataset with per-case ground truth + evaluation benchmark (`scripts/reconcile_benchmark.py`) — measured match rate and **100% benchmark accuracy**, precision/recall/F1 | ✅ implemented |
| LangGraph workflow (plan → execute tools → analyze signals → interpret) over read-only finance tools | ✅ implemented (`backend/app/ai/graph/`) |
| LLM explanation layer — **AI is explanation-only**: it describes engine-computed facts and never calculates financial truth | ✅ implemented (`backend/app/ai/llm.py`) |
| AI reconciliation endpoint `POST /api/v1/ai/reconcile` (+ `POST /api/v1/ai/chat`) | ✅ implemented |
| Frontend reconciliation dashboard (summary cards, match breakdown, typed exception table, opt-in AI narrative) | ✅ implemented (`frontend/src/pages/ReconciliationPage.jsx`) |
| Backend test suite | ✅ **728+ passing** (`pytest`; count subject to the latest run) |

Ground truth exists only in the synthetic generator, tests, and the benchmark — never as an input to the engine or any serving path.

Still deferred: human-approval workflow, bank-statement (third-source) ingestion, authentication/RBAC, production deployment, frontend test framework — see [Deferred phases](#deferred-phases-intentionally-not-implemented).

---

## Architecture

```
React + Vite  ──REST/JSON──▶  FastAPI Backend
                                  │
                ┌─────────────────┴─────────────────┐
                ▼                                   ▼
      Deterministic Reconciliation          AI Agent Layer
          Engine (no LLM math)             (LangGraph + LLM,
                │                           read-only tools)
                └─────────────────┬─────────────────┘
                                  ▼
                             PostgreSQL
                                  ▼
                    CSV / Razorpay data sources
```

### Non-negotiable responsibility split

| Concern | Owner |
|---|---|
| Calculations, matching, reconciliation, financial facts | **Deterministic code only** |
| Investigation, explanation, recommendation | **AI agent only** |
| Approval, rejection, final resolution | **Human only** |

No phase may violate this separation.

---

## Repository structure

```
razorpay/                          # monorepo root
├── backend/
│   ├── app/
│   │   ├── main.py                # application factory, CORS, error handling, lifespan logging
│   │   ├── api/health.py          # GET /health (liveness), GET /readiness (config probe)
│   │   ├── core/config.py         # pydantic-settings, env-driven, SecretStr credentials
│   │   └── db/                    # empty until Phase 2 (schema + Alembic live here)
│   ├── tests/                     # pytest suite (startup, health, readiness, config, CORS)
│   ├── requirements.txt
│   ├── .env.example               # backend configuration template
│   └── README.md
├── frontend/
│   ├── src/
│   │   ├── api/client.js          # API abstraction (base URL from env, timeout, JSON handling)
│   │   ├── hooks/useBackendHealth.js  # status state machine: checking/connected/unavailable
│   │   ├── pages/SystemStatusPage.jsx # app shell showing live "Backend Status"
│   │   ├── components/StatusPill.jsx
│   │   ├── App.jsx / main.jsx / index.css
│   ├── package.json               # React 19, Vite 8, Tailwind CSS 4, Recharts 3
│   ├── vite.config.js
│   ├── .env.example               # frontend configuration template
│   └── README.md
├── data/                          # synthetic datasets + generator (Phase 3) — empty by design
├── docs/
│   └── ReconAgent_Master_Spec.md  # master specification (source of truth)
├── tests/                         # cross-cutting/E2E tests (later phases) — empty by design
├── scripts/                       # automation scripts (added when needed)
├── infra/                         # deployment artifacts (deliberately minimal until required)
├── .env.example                   # union reference of all environment variables
├── .gitignore
└── README.md
```

---

## Phase roadmap

| Phase | Goal | Status |
|---|---|---|
| **1. Project Setup** | Repo structure, environments; FastAPI + React skeletons boot and talk via `/health` | **Complete** |
| 2. Database | PostgreSQL schema via SQLAlchemy + Alembic | **Complete** |
| 3. Synthetic Data | Labeled dataset for evaluation | **Complete** (seeded 100-record reconciliation batch; CSV datasets superseded by Razorpay API sync) |
| 4. Data Ingestion | Provider ingestion + validation | **Complete** (Razorpay API sync with upserts; CSV upload endpoints not built) |
| 5. Deterministic Matching Engine | Matching, tolerance, match rate | **Complete** (Track 04 payments-vs-settlements loop with rules R0–R9) |
| 6. Exception Management | Exception queue + tracking | Partial (typed exception report/table; no approval queue yet) |
| 7–8. AI Investigation | Agent tools, LangGraph state machine | Mostly complete (read-only tools, LangGraph workflow, signal analysis, LLM explanation) |
| 9. Human Approval | Approve/reject/resolve workflow | Deferred |
| 10. Dashboard | React dashboard | Partial (reconciliation dashboard live; full KPI/queue views pending) |
| 11. Razorpay Integration | Exploratory feasibility assessment | **Complete** (production-style client + sync verified with mocks/fakes) |
| 12–13. Testing & Demo Prep | Extended test suites + demo rehearsal | In progress (728+ backend tests passing) |

---

## Prerequisites

| Tool | Version required | Verified on this repo |
|---|---|---|
| Git | ≥ 2.40 | 2.50.1 |
| Python | 3.11+ | 3.12.10 |
| Node.js | 20+ (LTS) | 24.12.0 |
| npm | 10+ | 11.6.2 |
| PostgreSQL | 14+ | **not required for Phase 1** — needed from Phase 2 |

> Windows PowerShell examples below; macOS/Linux equivalents noted where they differ.

---

## Setup

### 1. Environment configuration

Copy templates — never commit your `.env` files:

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env
```

Then edit `backend\.env`:
- Replace `CHANGE_ME` values (`JWT_SECRET`, `DATABASE_URL` password).
- All credentials are optional; the service boots and the demo runs without them.
- **Zero-credential Track 04 demo:** the reconciliation dashboard loads a seeded synthetic batch by default — Razorpay keys are only needed for live API sync.
- Generate a strong JWT secret:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend (second terminal)

```powershell
cd frontend
npm install
npm run dev
```

- Frontend dev server: http://localhost:5173
- Backend API: http://localhost:8000
- OpenAPI docs: http://localhost:8000/docs · ReDoc: `/redoc` · schema: `/openapi.json`

The page shows **Backend Status: Connected / Unavailable**, driven by a real request to `GET /health` (never hardcoded).

### Demo flow (no credentials required)

1. Open http://localhost:5173 — the **Batch Reconciliation** tab is the default and loads the **Synthetic Demo Dataset** (seeded 100-record batch) automatically.
2. Engine-computed facts render immediately: summary cards, match breakdown, and the typed exception table — every exception row now carries a **deterministic severity** (CRITICAL/HIGH/MEDIUM/LOW), a **priority rank** (100/80/60/30/10, sorted descending by default with a severity filter), and a **recommended action**, all computed by the backend's triage policy (`app/services/reconciliation_policy.py`), never by an LLM.
3. Flip **AI explanation** to request an LLM narrative (needs `LLM_API_KEY`; the deterministic report is unaffected without it).
4. The **Reconciliation Evaluation** card shows measured quality for the same batch — accuracy, precision/recall/F1, FP/FN, throughput — from the evaluation-only surface `GET /api/v1/ai/reconcile/evaluation` (isolated ground truth; the serving API never sees it).
5. The **AI Assistant** tab asks natural-language questions through the existing LangGraph endpoint `POST /api/v1/ai/chat`: the agent plans Finance Tools, runs them deterministically, derives signals, and only then narrates. Without an LLM key it answers 503 and the UI explains what is missing.
6. **System Status** tab shows backend connectivity. Razorpay API integration stays optional — its endpoints answer with a sanitized `not_configured` response until keys are set.

---

## Development commands

| Command | Purpose |
|---|---|
| `cd backend; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --reload` | Run backend with hot reload |
| `cd frontend; npm run dev` | Run Vite dev server |
| `cd frontend; npm run build` | Production build |
| `cd frontend; npm run preview` | Preview the production build |

---

## Testing

Backend (pytest) — from `backend/` with the venv active:

```powershell
.\.venv\Scripts\python.exe -m pytest -q        # or simply: pytest
```

Covers: application startup/lifespan, `/health` and `/readiness` contracts, settings loading, CORS, Razorpay client/sync/repositories, finance metrics and APIs, the deterministic reconciliation engine (rules R0–R9, refund integrity, expected settlement, compound exceptions), the deterministic triage policy (severity/priority/recommended actions per exception type), the synthetic dataset/ground-truth alignment, the LangGraph workflow safety properties, and the AI endpoints — currently **753 passing tests** (count subject to the latest run).

Machine-readable evaluation: `backend/.venv/Scripts/python.exe scripts/reconcile_benchmark.py --json` prints the same payload as `GET /api/v1/ai/reconcile/evaluation` — accuracy/precision/recall/F1, TP/FP/FN, throughput and exception distribution measured against isolated ground truth (evaluation-only; serving responses keep `accuracy=null`).

Frontend: no automated frontend test framework is configured yet. The UI surface is still presentation-only; Vitest + Testing Library will be added when component logic grows beyond rendering engine output.

---

## Health vs readiness

| Endpoint | Meaning | Behavior |
|---|---|---|
| `GET /health` | **Liveness** — process is up | Always cheap, dependency-free. Returns `200 {"status": "ok"}`. |
| `GET /readiness` | **Readiness** — safe to receive traffic | Reports per-dependency *configuration* status (booleans only, never secret values). Returns `503 {"status": "unavailable", "issues": [...]}` on misconfiguration (e.g., missing `JWT_SECRET` in production). Live dependency connectivity checks are deferred to Phase 2 with the database layer. |

Phase 1 deliberately does **not** require PostgreSQL or any external service.

All future domain APIs will be versioned under `/api/v1/...`. CORS origins come from `CORS_ORIGINS` (development default: `http://localhost:5173`); unrestricted CORS must never ship to production.

---

## Environment variables

Annotated templates: [`backend/.env.example`](backend/.env.example), [`frontend/.env.example`](frontend/.env.example), union reference: [`.env.example`](.env.example).

| Variable | Consumed by | Required from | Example |
|---|---|---|---|
| `APP_NAME` | backend | Phase 1 | `ReconAgent API` |
| `VERSION` | backend | Phase 1 | `0.1.0` |
| `ENVIRONMENT` | backend | Phase 1 | `development` (`test`/`production`) |
| `CORS_ORIGINS` | backend | Phase 1 | `http://localhost:5173` (comma-separated) |
| `DATABASE_URL` | backend | Phase 2 | `postgresql://user:pass@localhost:5432/reconagent` |
| `JWT_SECRET` | backend | auth phase (mandatory in `production`) | `<64-char random hex>` |
| `LLM_API_KEY` | backend | AI foundation (mandatory in `production` validation) | *(provider key)* |
| `LLM_MODEL` | backend | AI foundation | `gpt-4o-mini` |
| `LLM_BASE_URL` / `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | backend | AI foundation (optional overrides) | *(unset)* / `30` / `2` |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | backend | integration phase | *(optional until then)* |
| `VITE_API_BASE_URL` | frontend | Phase 1 | `http://localhost:8000` |

### Secrets policy

- Never commit real secrets. Only `.env.example` files are tracked (enforced by `.gitignore`).
- Credentials are loaded as `SecretStr` so they never leak through logs/repr.
- Rotate any credential suspected of exposure immediately.

---

## Phase 1 Definition of Done

- [x] Monorepo structure clean and committed
- [x] Strong `.gitignore` (Python, Node/Vite, env/secrets, IDE, OS, caches)
- [x] Environment templates without real secrets
- [x] Enterprise-quality documentation
- [x] Existing work preserved (`docs/ReconAgent_Master_Spec.md`)
- [x] Backend FastAPI skeleton boots (application factory, lifespan logging, consistent JSON errors)
- [x] Frontend React+Vite+Tailwind shell boots
- [x] `GET /health` returns `{"status": "ok"}`
- [x] Frontend displays live backend status (Connected / Unavailable) from a real API call
- [x] Environment-based configuration loading works (pydantic-settings, `.env` support)
- [x] CORS configured per-environment for local development
- [x] Backend tests pass (now 728+); frontend build passes

---

## Deferred phases (intentionally NOT implemented)

Human-approval workflow (approve/reject/resolve + audit trail), bank-statement/third-source ingestion, CSV upload endpoints, authentication/RBAC, production deployment/Docker/Kubernetes infrastructure, background job infrastructure (Celery/Redis), frontend test framework.

These remain open per the master spec's later phases; everything already implemented is covered in [Current status](#current-status).

## Next step

**Exception management:** build the human-approval workflow on top of the existing typed exception report (queue, approve/reject/resolve decisions, audit trail) — the reconciliation engine and dashboard already produce the exception list it would operate on.

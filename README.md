# ReconAgent

**AI Finance Controller — Multi-Source Reconciliation & Exception Intelligence**

ReconAgent is an enterprise-grade, AI-assisted financial reconciliation platform. It ingests three data sources — payment/settlement records, bank statements, and the internal ledger — matches them with **deterministic, auditable code**, hands only the mismatches ("exceptions") to an **AI agent** that investigates, explains, and recommends resolutions, and requires an explicit **human decision** before anything is marked resolved. Every step is written to an audit trail.

The authoritative product/technical blueprint is [`docs/ReconAgent_Master_Spec.md`](docs/ReconAgent_Master_Spec.md).

---

## Current status

**Phase 1 (Project Setup) is implemented and verified on this machine:**

| Check | Result |
|---|---|
| Backend boots (`uvicorn app.main:app`) | ✅ verified |
| `GET /health` → `200 {"status": "ok"}` | ✅ verified |
| `GET /readiness` configuration probe | ✅ verified (works with no database/secrets configured) |
| Backend test suite | ✅ 34/34 passing (`pytest`) |
| Frontend production build (`npm run build`) | ✅ passes cleanly |
| Frontend → backend health call wiring | ✅ implemented (real `fetch` to `GET /health`; status rendered dynamically) |

Later phases (database schema, ingestion, matching, AI, approvals, dashboard) are **not** implemented — see [Deferred phases](#deferred-phases-intentionally-not-implemented).

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
| **1. Project Setup** | Repo structure, environments; FastAPI + React skeletons boot and talk via `/health` | **Complete (this commit)** |
| 2. Database | PostgreSQL schema via SQLAlchemy + Alembic | Deferred |
| 3. Synthetic Data | 50+ record labeled dataset across 3 CSVs | Deferred |
| 4. Data Ingestion | CSV upload endpoints + validation | Deferred |
| 5. Deterministic Matching Engine | Matching, tolerance, match rate | Deferred |
| 6. Exception Management | Exception queue + tracking | Deferred |
| 7–8. AI Investigation | Agent tools, LangGraph state machine | Deferred |
| 9. Human Approval | Approve/reject/resolve workflow | Deferred |
| 10. Dashboard | Full React dashboard (KPIs, queue, detail views) | Deferred |
| 11. Razorpay Integration | Exploratory feasibility assessment | Deferred |
| 12–13. Testing & Demo Prep | Extended test suites + demo rehearsal | Deferred |

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
- All credentials are optional for Phase 1; the service boots without them.
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

Covers: application startup/lifespan, `/health` contract, `/readiness` behavior, settings loading/validation, and CORS preflight/origin enforcement (34 tests).

Frontend: no automated frontend test framework is configured yet. This is intentional for Phase 1 (minimal dependencies; the UI surface is one status view). Vitest + Testing Library will be added when component logic grows beyond the current shell.

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
| `LLM_API_KEY` | backend | AI phases (mandatory in `production` validation) | *(provider key)* |
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
- [x] Backend tests pass (34/34); frontend build passes

---

## Deferred phases (intentionally NOT implemented)

Database schema and migrations (SQLAlchemy models, Alembic), CSV ingestion, synthetic data generation, reconciliation/matching logic, exception workflows, LLM/AI/LangGraph code, agent tools, Razorpay integration, Celery/Redis/background jobs, authentication/RBAC, audit logging, full dashboard, frontend test framework, deployment/Docker/Kubernetes infrastructure.

These belong to Phases 2–13 per the master spec. Nothing above should be built ahead of its phase.

## Next step

**Phase 2 — Database:** define the SQLAlchemy models for the schema in master spec §19, configure Alembic migrations, wire a PostgreSQL session/engine module into `backend/app/db/`, extend `/readiness` with a live database connectivity check.

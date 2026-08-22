# ReconAgent Backend

Python / FastAPI service for ReconAgent.

**Status: Phase 1 skeleton implemented.** Application factory, environment-based configuration, health/readiness probes, CORS, structured error handling, and a pytest suite. No business logic, database schema, or external integrations yet — those arrive in Phases 2+.

## Stack

- Python 3.11+, FastAPI, Uvicorn
- pydantic / pydantic-settings (environment-driven configuration, `SecretStr` credentials)
- pytest + httpx (TestClient) for tests
- SQLAlchemy + Alembic + PostgreSQL: **Phase 2** (see `app/db/`, currently empty)

## Layout

```
backend/
├── app/
│   ├── main.py          # create_app() factory, CORS, exception handler, lifespan logging
│   ├── api/health.py    # GET /health (liveness), GET /readiness (config probe)
│   ├── core/config.py   # Settings via pydantic-settings (.env aware)
│   └── db/              # reserved for Phase 2 schema/session/migrations
├── tests/               # startup, health, readiness, config, CORS coverage
├── requirements.txt
└── .env.example         # copy to .env; see file for every variable
```

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Conventions: domain APIs will be versioned under `/api/v1/...`; configuration comes only from environment/`.env` — never hardcoded; secrets never logged.

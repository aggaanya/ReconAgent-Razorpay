# ReconAgent — Technology Stack & System Architecture

**Status:** Living architectural reference
**Audience:** Developers, technical leads, and AI coding agents working on ReconAgent
**Scope:** Target architecture and phased implementation plan for an AI-assisted financial reconciliation platform

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Goals](#2-project-goals)
3. [Core Architectural Principles](#3-core-architectural-principles)
4. [Complete Technology Stack](#4-complete-technology-stack)
5. [Technology Responsibilities](#5-technology-responsibilities)
6. [High-Level System Architecture](#6-high-level-system-architecture)
7. [Frontend Architecture](#7-frontend-architecture)
8. [Backend Architecture](#8-backend-architecture)
9. [Database Architecture](#9-database-architecture)
10. [Data Processing Architecture](#10-data-processing-architecture)
11. [Reconciliation Engine Architecture](#11-reconciliation-engine-architecture)
12. [AI Architecture](#12-ai-architecture)
13. [LangGraph Agent Architecture](#13-langgraph-agent-architecture)
14. [Agent Tools](#14-agent-tools)
15. [External Integrations (Razorpay)](#15-external-integrations-razorpay)
16. [Authentication Architecture](#16-authentication-architecture)
17. [Background Processing Architecture](#17-background-processing-architecture)
18. [Reporting Architecture](#18-reporting-architecture)
19. [Data Flow](#19-data-flow)
20. [Request Flow](#20-request-flow)
21. [Reconciliation Flow](#21-reconciliation-flow)
22. [AI Investigation Flow](#22-ai-investigation-flow)
23. [Human Approval Flow](#23-human-approval-flow)
24. [End-to-End System Flow](#24-end-to-end-system-flow)
25. [Project Directory Structure](#25-project-directory-structure)
26. [Environment Configuration](#26-environment-configuration)
27. [API Communication](#27-api-communication)
28. [Database Communication](#28-database-communication)
29. [Security Architecture](#29-security-architecture)
30. [Error Handling](#30-error-handling)
31. [Observability and Logging](#31-observability-and-logging)
32. [Scalability Considerations](#32-scalability-considerations)
33. [Reliability Considerations](#33-reliability-considerations)
34. [Enterprise Design Considerations](#34-enterprise-design-considerations)
35. [Phase-by-Phase Architecture Evolution](#35-phase-by-phase-architecture-evolution)
36. [What Is Implemented Now](#36-what-is-implemented-now)
37. [What Will Be Added Later](#37-what-will-be-added-later)
38. [Architecture Decisions](#38-architecture-decisions)
39. [Technology Decision Table](#39-technology-decision-table)
40. [MVP vs Enterprise](#40-mvp-vs-enterprise)
41. [Definition of Done](#41-definition-of-done)
42. [Final Architecture Diagram](#42-final-architecture-diagram)

---

## 1. Executive Summary

ReconAgent is an AI-assisted financial reconciliation platform. It ingests financial records from multiple sources — payment gateway data (initially Razorpay), bank transaction data, and internal ledger/invoice data — and determines which records match and which do not.

The defining architectural rule of ReconAgent is the separation between **deterministic computation** and **AI-assisted reasoning**:

```
DETERMINISTIC CODE  →  calculates financial facts
        ↓
       AI            →  investigates unexplained exceptions
        ↓
      HUMAN           →  approves or rejects recommendations
```

All arithmetic, matching, and financial truth is produced by deterministic application code. The AI layer never performs authoritative financial calculations — it only reasons over evidence that deterministic code has already produced, and its output is always a **recommendation**, never a **fact**. A human remains the final decision-maker for any exception resolution.

This document is the architectural source of truth for ReconAgent. It describes the target system, the technology stack, how components communicate, and the phased plan by which the system will be built. It does not claim that unimplemented components already exist — every section distinguishes **CURRENT**, **PLANNED**, **OPTIONAL**, and **FUTURE** capabilities.

---

## 2. Project Goals

- Reconcile payment, bank, and ledger records accurately and deterministically.
- Automatically identify exceptions (unmatched or partially matched records).
- Use an AI agent to investigate exceptions and propose likely explanations and recommendations, backed by retrieved evidence rather than free-form guessing.
- Keep a human in the loop for all final decisions affecting financial records.
- Maintain a complete audit trail of matches, exceptions, AI recommendations, and human approvals.
- Start with a lean MVP (CSV-based, no AI) and evolve toward an enterprise-grade platform (live integrations, AI investigation, RBAC, background processing, observability) without requiring an architectural rewrite at any phase.
- Ensure the reconciliation engine's correctness never depends on the AI being available, correct, or even present.

---

## 3. Core Architectural Principles

1. **Deterministic financial computation.** All amounts, differences, and match/no-match decisions are computed by ordinary application code (Python), not by an LLM.
2. **AI-assisted investigation, not AI-authoritative computation.** The AI explains and recommends; it does not decide or calculate.
3. **Human-in-the-loop.** No AI recommendation mutates a financial record without explicit human approval.
4. **Separation of concerns.** Presentation (React), API (FastAPI), business logic (services), data access (repositories), and persistence (PostgreSQL) are distinct layers.
5. **Auditability and traceability.** Every match, exception, AI investigation, and human decision is recorded and attributable.
6. **Least privilege.** The frontend never touches the database directly; the AI agent never has unrestricted database access — it can only call defined tools.
7. **Progressive enhancement.** The system starts as a minimal, correct MVP and grows in phases toward enterprise capabilities (auth, background jobs, live integrations, observability) without discarding earlier work.

---

## 4. Complete Technology Stack

### Frontend
- React
- Vite
- Tailwind CSS
- Recharts

### Backend
- Python
- FastAPI
- Uvicorn
- Pydantic / Pydantic Settings

### Database
- PostgreSQL

### ORM / Database Access
- SQLAlchemy

### Database Migrations
- Alembic

### Data Processing
- Pandas

### AI / LLM
- LLM with tool calling

### Agent Orchestration
- LangGraph

### Authentication
- JWT

### Background Processing
- Celery
- Redis

### Data Sources
- CSV (initial)
- Razorpay APIs (later)

### Version Control
- Git, GitHub

### Containerization
- Docker (when appropriate)

> **Important:** Not all of the above are required for the MVP. See [Section 40 — MVP vs Enterprise](#40-mvp-vs-enterprise) for what is required now versus later.

---

## 5. Technology Responsibilities

| Technology | Responsibility |
|---|---|
| React | Renders UI, manages client-side state and user interaction |
| Vite | Frontend dev server and build tool |
| Tailwind CSS | Utility-first styling, consistent design system |
| Recharts | Renders financial charts (match rate, trends, exception volume) |
| FastAPI | Exposes versioned REST API, request validation, routing |
| Uvicorn | ASGI server that runs the FastAPI application |
| Pydantic | Request/response schema validation and settings management |
| PostgreSQL | Authoritative persistent storage for all financial data |
| SQLAlchemy | Maps Python objects to database rows; issues parameterized queries |
| Alembic | Version-controls and applies database schema migrations |
| Pandas | Loads, cleans, normalizes, and validates CSV data before persistence |
| LLM | Reasons over structured evidence to explain exceptions |
| LangGraph | Orchestrates the multi-step AI investigation workflow as a stateful graph |
| JWT | Stateless authentication token for API requests (planned) |
| Celery | Executes background/asynchronous jobs (planned) |
| Redis | Message broker for Celery and general caching (planned) |
| Razorpay API | Source of live payment/settlement data (planned) |

---

## 6. High-Level System Architecture

```
                              USER
                               |
                               v
                        React + Vite
                               |
                               | HTTPS / REST (JSON)
                               v
                       FastAPI Backend
                               |
             +-----------------+------------------+
             |                 |                  |
             v                 v                  v
      Reconciliation       AI Agent          Reporting
         Engine             Layer              Engine
             |                 |
             |                 v
             |           LLM + Tools
             |            (LangGraph)
             |                 |
             v                 v
                    PostgreSQL (single source
                     of persisted truth)
                               |
                   +-----------+-----------+
                   |                       |
                   v                       v
               CSV Data              Razorpay API
             (current)                (planned)
```

**Key rule shown in this diagram:** the React frontend only ever talks to FastAPI. FastAPI is the sole gateway to PostgreSQL, the AI layer, and external integrations such as Razorpay.

---

## 7. Frontend Architecture

### Layering

```
React
  ↓
Components  (buttons, tables, charts, forms)
  ↓
Pages       (Dashboard, Exceptions, Reconciliation Runs, Reports)
  ↓
API client  (typed wrapper over fetch/axios)
  ↓
FastAPI
```

### Responsibilities

- **React**: component-based UI, state management, rendering reconciliation data, exceptions, and recommendations for human review.
- **Vite**: fast local dev server, hot module reload, production build/bundling.
- **Tailwind CSS**: consistent, utility-driven styling without hand-written CSS sprawl.
- **Recharts**: renders match-rate trends, exception volume, and other financial visualizations from data already computed by the backend.

### Supporting concerns

- **Frontend environment variables**: e.g. `VITE_API_BASE_URL`, injected at build/runtime, never containing secrets.
- **API client abstraction**: a single module responsible for constructing requests, attaching auth headers (once JWT is implemented), and handling response/error shapes consistently — pages never call `fetch` directly.
- **Loading states**: every data-fetching view has an explicit loading state; the UI never presents stale data as if it were current.
- **Error states**: API errors are surfaced as readable messages, not raw stack traces or raw JSON.
- **Authentication token handling (planned)**: once JWT auth exists, the API client attaches the token to outgoing requests and handles 401 responses (e.g., redirect to login). This is **not implemented in the MVP**.

### Explicit boundary

The frontend **never**:
- Connects directly to PostgreSQL.
- Calls the Razorpay API directly.
- Calls the LLM directly.

All of these are mediated by the FastAPI backend.

---

## 8. Backend Architecture

### Conceptual directory layout

```
backend/
└── app/
    ├── main.py            # FastAPI app instantiation, router registration
    ├── api/                # Route handlers (thin controllers)
    ├── core/                # Config, settings, security utilities
    ├── schemas/             # Pydantic request/response models
    ├── models/               # SQLAlchemy ORM models
    ├── repositories/          # Data-access layer (queries against models)
    ├── services/               # Business logic (reconciliation, exceptions, reporting)
    ├── agents/                   # LangGraph graph definitions, agent tools
    ├── integrations/               # External API adapters (Razorpay, etc.)
    └── db/                           # Session management, base classes
```

### Layered request handling

```
API layer
  ↓
Service layer
  ↓
Repository / data-access layer
  ↓
PostgreSQL
```

- **API layer**: parses/validates requests (via Pydantic schemas), delegates to services, formats responses. Contains no business logic.
- **Service layer**: contains the actual business rules — e.g., how a reconciliation run is executed, how an exception is classified.
- **Repository layer**: encapsulates all SQLAlchemy queries; services never write raw queries inline.

### Reconciliation-specific flow

```
API layer
  ↓
Reconciliation service
  ↓
Matching engine
```

### AI-specific flow

```
Exception
  ↓
Agent service
  ↓
LangGraph
  ↓
Tools
  ↓
LLM
```

### Explicit boundary

Route handlers (in `api/`) must remain thin. Business logic — matching rules, exception classification, agent invocation — belongs in `services/`, never directly inside a route function. This keeps the API layer testable, swappable, and free of duplicated logic.

---

## 9. Database Architecture

```
FastAPI
   ↓
SQLAlchemy
   ↓
PostgreSQL
```

### Why PostgreSQL

PostgreSQL is chosen because ReconAgent is a financial system that requires: strong transactional (ACID) guarantees, relational integrity between payments/bank records/ledger entries, mature tooling for migrations and indexing, and reliable numeric/decimal types for monetary values.

### Conceptual tables

| Table | Purpose |
|---|---|
| `users` | Application users (planned once auth exists) |
| `payments` | Records from the payment gateway (e.g., Razorpay) |
| `bank_transactions` | Records from bank statements/feeds |
| `ledger_entries` | Internal accounting/invoice records |
| `reconciliation_runs` | Metadata for each reconciliation execution (time, source data, status) |
| `reconciliation_results` | Per-record outcome of a run: matched, unmatched, partially matched |
| `exceptions` | Unmatched or discrepant records requiring investigation |
| `agent_investigations` | Record of each AI investigation performed on an exception |
| `recommendations` | AI-generated recommendation tied to an investigation |
| `approvals` | Human decision (approve/reject) on a recommendation |
| `audit_logs` | Immutable log of significant state-changing actions |

Fields for these tables are intentionally not fully enumerated here; they should be defined in `models/` during Phase 2 and are considered **proposed** unless documented separately.

### Roles of the three data-layer technologies

- **SQLAlchemy** — maps Python objects to database rows/tables (application ↔ database mapping).
- **Alembic** — version-controls and applies schema changes (schema migration/version control).
- **PostgreSQL** — the actual persistent, authoritative database engine.

### Explicit boundary

The frontend must never connect directly to PostgreSQL. All access goes through FastAPI → SQLAlchemy. This preserves validation, authorization, and audit logging at the API boundary, and prevents the database schema from becoming a public contract.

---

## 10. Data Processing Architecture

```
CSV
 ↓
Pandas
 ↓
Normalization
 ↓
Validation
 ↓
Database
 ↓
Reconciliation
```

### What Pandas does

- Loads CSV files into structured data frames.
- Cleans malformed or inconsistent rows.
- Normalizes formats (dates, currency amounts, reference IDs).
- Performs type conversion (e.g., strings to decimals).
- Supports validation before persistence.
- Assists with exploratory analysis during development.

### Explicit boundary

Pandas is a **processing** tool, not a **storage** tool. Once data is cleaned and validated, it is persisted into PostgreSQL, which remains the single, authoritative source of truth for the application. Pandas data frames are transient and never queried by the rest of the system after ingestion completes.

---

## 11. Reconciliation Engine Architecture

The reconciliation engine is **fully deterministic**. No AI is involved in computing matches or differences.

```
Payment
   |
Bank Transaction
   |
Ledger Entry
   |
Normalize
   |
Compare
   |
Match / Exception
```

### Example: matched record

```
Payment = ₹10,000
Bank    = ₹10,000
Ledger  = ₹10,000
Result  = MATCHED
```

### Example: exception with a computable difference

```
Payment = ₹10,000
Bank    = ₹9,700
Ledger  = ₹10,000

difference = Payment − Bank = ₹300   ← calculated by deterministic code
```

The deterministic engine computes `difference = ₹300`. It does **not** know or claim *why* the difference exists. That question — "is this a processing fee, a partial refund, a duplicate, etc.?" — is handed to the AI investigation layer as a downstream step, and only after the exception has been created.

**AI does not perform authoritative financial arithmetic.** Every amount, total, and difference presented anywhere in the system is produced by the reconciliation engine or reporting service, never by the LLM.

---

## 12. AI Architecture

```
Reconciliation Engine
        |
        v
Unmatched Record
        |
        v
   Exception
        |
        v
AI Investigation
        |
        v
Evidence Collection
        |
        v
       LLM
        |
        v
  Reason
  Confidence
  Explanation
  Recommendation
```

### Evidence-restricted reasoning

The AI never queries the database freely. It receives **structured evidence** — assembled by backend services and agent tools — such as the related payment, bank record, ledger entry, computed difference, and any relevant historical patterns (e.g., known fee schedules, prior similar exceptions).

### Output classification

AI output is always treated as a **RECOMMENDATION**:

- Reason (hypothesis for the discrepancy)
- Confidence (how certain the model is)
- Explanation (human-readable reasoning)
- Recommendation (proposed resolution, e.g., "classify as processing fee")

It is never treated as an **AUTHORITATIVE FINANCIAL FACT**. Only a human approval action (see [Section 23](#23-human-approval-flow)) can convert a recommendation into an applied resolution.

---

## 13. LangGraph Agent Architecture

### Why LangGraph

The AI investigation of an exception is not a single LLM call — it is a multi-step process involving evidence gathering, tool calls, branching logic (e.g., "if a matching refund exists, follow the refund path"), and a pause for human approval. LangGraph provides a structured way to model this as a graph of nodes and edges with explicit state, rather than as ad hoc chained prompts.

### Conceptual workflow

```
START
  ↓
Load Exception
  ↓
Collect Evidence
  ↓
Analyze
  ↓
Determine Root Cause
  ↓
Generate Recommendation
  ↓
Human Approval
  ↓
END
```

### Key concepts

- **State**: the evolving data structure carried through the graph (exception details, evidence gathered so far, intermediate conclusions).
- **Nodes**: discrete steps (e.g., "collect evidence," "call LLM," "generate recommendation").
- **Edges**: transitions between nodes, including conditional branches.
- **Branching**: e.g., routing to a "duplicate check" path vs. a "fee check" path depending on evidence.
- **Tool calls**: nodes that invoke agent tools (see [Section 14](#14-agent-tools)) to fetch evidence rather than hallucinating it.
- **Human approval**: a graph node that pauses execution pending a human decision.
- **Resumability**: the graph can persist state and resume after the human approval step, rather than needing to be re-run from scratch.

### Explicit boundary

LangGraph orchestrates the **AI investigation workflow**. It is not, and does not replace, the reconciliation engine. The reconciliation engine's matching logic remains deterministic Python code outside of LangGraph.

---

## 14. Agent Tools

Conceptual tools available to the agent (exposed as callable functions, not direct database access):

- `get_transaction()`
- `get_settlement()`
- `get_bank_record()`
- `get_invoice()`
- `calculate_difference()`
- `find_related_transactions()`
- `check_fee()`
- `check_refund()`
- `check_duplicate()`
- `generate_reconciliation_note()`

### Tool call flow

```
Agent
 ↓
Tool
 ↓
Backend service / repository
 ↓
Database
 ↓
Evidence
 ↓
Agent
```

### Explicit boundary

The agent does not receive unrestricted database credentials. Every tool is a narrow, purpose-built function that goes through existing backend services/repositories, which enforce the same validation and access rules as the rest of the application. This bounds what the AI can retrieve and prevents it from executing arbitrary queries.

---

## 15. External Integrations (Razorpay)

### Current (CSV-based)

```
CSV / Synthetic Data
        ↓
Reconciliation
```

### Planned (live integration)

```
Razorpay API
        ↓
Data Adapter
        ↓
Normalization
        ↓
Reconciliation
```

### Adapter boundary

A dedicated integration/adapter layer (`integrations/razorpay/`) translates Razorpay-specific API responses into the same normalized internal representation used by CSV-based data. The reconciliation engine and downstream services depend only on this normalized representation — never on Razorpay-specific fields or response shapes directly.

### Why this matters

- **Testability**: reconciliation logic can be tested against CSV fixtures without needing live Razorpay credentials or network access.
- **Resilience to API changes**: if Razorpay's API changes, only the adapter needs updating.
- **Source flexibility**: additional payment gateways could be added later behind the same adapter boundary without touching the reconciliation engine.

---

## 16. Authentication Architecture

> **Status: PLANNED.** Not implemented in the MVP. This section documents the target architecture only.

```
User
 ↓
React
 ↓
JWT authentication
 ↓
FastAPI
 ↓
Authorization
 ↓
API / service
```

### Authentication vs. authorization

- **Authentication** establishes *who* the user is (via a JWT issued at login).
- **Authorization** establishes *what* that authenticated user is allowed to do (role-based access control).

### Future roles (conceptual)

- **Admin** — full system access, user management.
- **Finance Controller** — can approve/reject AI recommendations, view all reconciliation data.
- **Finance Analyst** — can view reconciliation data and exceptions, limited approval rights.

This document describes the intended shape of authentication; it does not implement it.

---

## 17. Background Processing Architecture

> **Status: OPTIONAL for MVP, PLANNED for enterprise use.**

```
Scheduler
   ↓
Celery
   ↓
Redis
   ↓
Worker
   ↓
Reconciliation
   ↓
Report
   ↓
Notification
```

### Roles

- **Celery**: executes background/asynchronous jobs — e.g., scheduled reconciliation runs, batch AI investigations, report generation — outside the request/response cycle of the API.
- **Redis**: acts as the message broker between the API/scheduler and Celery workers (and may also serve as a general-purpose cache).

Background processing is not required for the MVP, where reconciliation can run synchronously or via a manually triggered script. It becomes valuable once reconciliation runs are scheduled, large, or need to run without blocking API requests.

---

## 18. Reporting Architecture

```
PostgreSQL
   ↓
Reporting Service
   ↓
Summary
   ↓
CSV / PDF / API
   ↓
React Dashboard
```

### Possible metrics

- Total records processed
- Matched count
- Unmatched count
- Exceptions count
- Match rate (%)
- Auto-resolved count
- Sent to human review count
- Unresolved count

### Explicit boundary

All metrics are computed by the reporting service directly from PostgreSQL data using deterministic aggregation. The LLM is never responsible for calculating these figures — it may only be quoted or referenced within qualitative notes attached to individual exceptions, never within summary statistics.

---

## 19. Data Flow

```
CSV / Razorpay API
        ↓
   Ingestion (Pandas)
        ↓
   Normalization / Validation
        ↓
      PostgreSQL
        ↓
  Reconciliation Engine
        ↓
  Matched / Exceptions
        ↓
  (Exceptions only) AI Investigation
        ↓
  Human Approval
        ↓
  Resolution + Audit Log
        ↓
  Reporting / Dashboard
```

---

## 20. Request Flow

```
React (user action)
   ↓  HTTPS / REST (JSON)
FastAPI route handler
   ↓
Pydantic request validation
   ↓
Service layer (business logic)
   ↓
Repository layer (SQLAlchemy)
   ↓
PostgreSQL
   ↓
Repository → Service → API layer
   ↓
Pydantic response serialization
   ↓  HTTPS / REST (JSON)
React (render result)
```

---

## 21. Reconciliation Flow

```
1. Reconciliation run is triggered (manually or scheduled).
2. Relevant payment, bank, and ledger records are loaded from PostgreSQL.
3. Records are normalized into a comparable form.
4. Deterministic matching rules compare records (amount, reference, tolerance).
5. Matches are recorded as `reconciliation_results` with status MATCHED.
6. Non-matches are recorded as `reconciliation_results` with status UNMATCHED
   and a corresponding `exceptions` row is created.
7. Run metadata (counts, duration, status) is stored in `reconciliation_runs`.
```

---

## 22. AI Investigation Flow

```
1. An exception is loaded by the agent service.
2. LangGraph begins the investigation graph with the exception as initial state.
3. Evidence-gathering nodes call agent tools to fetch related records.
4. The LLM analyzes the collected evidence (not raw, unrestricted data).
5. The LLM produces a reason, confidence score, explanation, and recommendation.
6. The investigation and recommendation are persisted
   (`agent_investigations`, `recommendations`).
7. The graph pauses at the human-approval node.
```

---

## 23. Human Approval Flow

```
1. A Finance Analyst/Controller views the exception and AI recommendation in the UI.
2. The human reviews the evidence and the AI's reasoning.
3. The human approves or rejects the recommendation (`approvals` table).
4. If approved, the resolution is applied to the exception's status
   (e.g., marked as "explained: processing fee").
5. If rejected, the exception remains open for manual handling or further investigation.
6. An `audit_logs` entry is created recording who decided what, and when.
```

The AI never writes a resolution directly — the `approvals` step is the only path by which an exception's status changes based on AI input.

---

## 24. End-to-End System Flow

```
 1. Payment data arrives (CSV now, Razorpay API later).
 2. Bank transaction data arrives.
 3. Internal ledger record exists.
 4. Data is normalized (Pandas → validated → persisted to PostgreSQL).
 5. Reconciliation engine compares records deterministically.
 6. Matching records are marked MATCHED.
 7. Unmatched records become exceptions.
 8. Exception evidence is collected via agent tools.
 9. LangGraph orchestrates the investigation workflow.
10. Tools retrieve relevant records through backend services.
11. The LLM analyzes the assembled evidence.
12. The AI generates a reason, confidence score, and recommendation.
13. A human reviews the recommendation in the dashboard.
14. The human approves or rejects it.
15. The resolution (if any) is recorded against the exception.
16. An audit log entry is created for the decision.
17. The reporting dashboard reflects the updated state (match rate, exceptions remaining, etc.).
```

---

## 25. Project Directory Structure

```
ReconAgent/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── api/
│   │   └── ...
│   ├── index.html
│   └── vite.config.js
├── backend/
│   └── app/
│       ├── main.py
│       ├── api/
│       ├── core/
│       ├── schemas/
│       ├── models/
│       ├── repositories/
│       ├── services/
│       ├── agents/
│       ├── integrations/
│       └── db/
├── data/            # sample/synthetic CSV data
├── docs/            # architecture and design docs (this file lives here)
├── tests/           # backend and frontend tests
├── scripts/         # one-off/utility scripts (e.g., data generation)
├── infra/           # Docker, deployment config (as needed)
├── .env.example
├── .gitignore
└── README.md
```

This structure is intentionally minimal at first; directories such as `infra/` are populated only when containerization/deployment work begins, not created empty for appearance.

---

## 26. Environment Configuration

| Variable | Required now (MVP) | Optional now | Required later |
|---|---|---|---|
| `DATABASE_URL` | ✅ | | |
| `VITE_API_BASE_URL` | ✅ | | |
| `LLM_API_KEY` | | ✅ (only once AI phase begins) | ✅ |
| `JWT_SECRET` | | | ✅ (auth phase) |
| `RAZORPAY_KEY_ID` | | | ✅ (integration phase) |
| `RAZORPAY_KEY_SECRET` | | | ✅ (integration phase) |

Example (`.env.example` — placeholders only, never real values):

```
DATABASE_URL=postgresql://user:password@localhost:5432/reconagent
JWT_SECRET=
LLM_API_KEY=
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
VITE_API_BASE_URL=http://localhost:8000
```

Real secrets are never committed to the repository or written into documentation; they are supplied via `.env` files (excluded via `.gitignore`) or a secrets manager in production.

---

## 27. API Communication

- **Protocol**: HTTPS in production, HTTP acceptable for local development.
- **Format**: JSON request/response bodies.
- **Validation**: all inbound requests validated against Pydantic schemas before reaching service logic.
- **Versioning**: API routes are namespaced (e.g., `/api/v1/...`) so breaking changes can be introduced without disrupting existing frontend clients.
- **CORS**: configured explicitly to allow only the known frontend origin(s).

---

## 28. Database Communication

- All database access goes through SQLAlchemy sessions managed in `db/`.
- Queries are parameterized by SQLAlchemy — raw string-concatenated SQL is not used, which inherently protects against SQL injection.
- Repositories are the only layer permitted to construct queries; services call repositories, never the database directly.
- Alembic migrations are the only sanctioned way to change schema — manual, ad hoc schema edits against the running database are not part of the workflow.

---

## 29. Security Architecture

- HTTPS enforced in production environments.
- JWT-based authentication (planned) with short-lived tokens.
- Role-based authorization (planned) gating sensitive actions (e.g., approving recommendations).
- Secrets managed via environment variables / secrets manager, never hardcoded or committed.
- CORS restricted to known frontend origins.
- Input validation enforced at the API boundary via Pydantic.
- SQL injection mitigated structurally through SQLAlchemy's parameterized queries.
- Audit logging for state-changing actions, especially approvals and resolutions.
- Least-privilege principle applied to both human roles and the AI agent's tool access.
- Strict API boundaries: frontend never touches PostgreSQL, Razorpay, or the LLM directly.
- **AI-generated recommendations never directly mutate financial records.** A recommendation only becomes an applied resolution after explicit human approval (see [Section 23](#23-human-approval-flow)).

---

## 30. Error Handling

```
Frontend
 ↓
API error (non-2xx response)
 ↓
FastAPI error response (structured JSON: code, message)
 ↓
Frontend user-friendly message
```

### Categories handled explicitly

- Validation errors (bad request shape/values)
- Authentication errors (planned)
- Authorization errors (planned)
- Not-found errors
- Integration failures (e.g., Razorpay API unavailable)
- Database failures
- AI/LLM failures (timeout, malformed output, tool failure)
- Background job failures (planned, once Celery is introduced)

Internal stack traces and raw exception details are never returned to the frontend/user; they are logged server-side only.

---

## 31. Observability and Logging

> **Status: mostly PLANNED**, becoming more important from Phase 8 onward.

```
Application
 ↓
Structured Logs
 ↓
Monitoring
 ↓
Alerts
```

Planned elements:

- Request IDs / correlation IDs threaded through logs for a given request or agent investigation.
- Structured (JSON) logging rather than free-text logs.
- Error tracking for unhandled exceptions.
- Job monitoring once Celery is introduced.
- Health checks (`/health`) and readiness checks for deployment orchestration.

### Application logs vs. audit logs

- **Application logs**: operational — errors, request timing, debugging information. Not necessarily permanent.
- **Audit logs**: business-critical — who approved/rejected what, and when. Immutable and retained as part of the financial record trail, stored in the `audit_logs` table rather than in log files.

---

## 32. Scalability Considerations

| Component | Scaling approach |
|---|---|
| Frontend | Static deployment behind a CDN |
| FastAPI | Multiple stateless application instances behind a load balancer |
| PostgreSQL | Indexing on frequently queried columns, connection pooling, read replicas if/when read load requires it |
| Celery | Multiple worker processes/instances (planned) |
| Redis | Standard broker infrastructure, scaled as job volume requires (planned) |
| AI/LLM | Controlled concurrency and rate limiting to manage cost and latency |

The MVP is intentionally not over-engineered for scale it does not yet need; these are documented as the natural scaling path, not as MVP requirements.

---

## 33. Reliability Considerations

- Reconciliation runs are idempotent where possible — re-running a run on the same input should not duplicate results.
- The AI investigation workflow is designed to be resumable (via LangGraph state), so a failure after evidence collection does not require restarting from scratch.
- Failures in the AI layer do not block or corrupt the deterministic reconciliation results — an exception can exist and be reviewed by a human even if AI investigation fails.
- Integration failures (e.g., Razorpay API downtime) are isolated to the ingestion step and do not affect previously persisted data.

---

## 34. Enterprise Design Considerations

1. **Separation of concerns** — presentation, API, business logic, and persistence are distinct layers.
2. **Deterministic financial computation** — all monetary calculations are code-driven, not model-driven.
3. **AI-assisted investigation** — the AI adds reasoning over exceptions, not authority over outcomes.
4. **Human-in-the-loop** — every AI recommendation requires human approval before it affects records.
5. **Auditability** — every state-changing action is logged and attributable.
6. **Traceability** — a resolved exception can be traced back through its investigation, evidence, and approval.
7. **Idempotency** — reconciliation and ingestion operations are designed not to produce duplicate effects on re-run.
8. **Security** — least privilege, validated input, protected secrets, controlled API surface.
9. **Least privilege** — applies to both human roles and AI tool access.
10. **Observability** — structured logs, correlation IDs, health checks (planned).
11. **Testability** — layered architecture (services/repositories) allows business logic to be tested independent of the API or database.
12. **Versioned APIs** — API routes are versioned to allow safe evolution.
13. **Database migrations** — schema changes are version-controlled via Alembic, never applied ad hoc.
14. **Failure isolation** — AI or integration failures do not compromise deterministic reconciliation results.
15. **Extensibility** — adapter boundaries (e.g., for Razorpay) allow new data sources or gateways to be added without rewriting core logic.

---

## 35. Phase-by-Phase Architecture Evolution

| Phase | Focus | Technologies introduced |
|---|---|---|
| **Phase 1** | Project setup | React + Vite, FastAPI, health endpoint, frontend/backend communication, configuration, testing scaffolding |
| **Phase 2** | Database & schema | PostgreSQL, SQLAlchemy, Alembic, database models, relationships, indexes |
| **Phase 3** | Synthetic data | Payment/bank/ledger sample data (50+ records), deliberate exception scenarios |
| **Phase 4** | Data ingestion | CSV, Pandas, normalization, validation, persistence |
| **Phase 5** | Reconciliation engine | Deterministic matching, amount comparison, reference matching, tolerance rules, match status |
| **Phase 6** | Exception management | Exception records, difference calculation, reason categories, review status |
| **Phase 7** | AI tools | Evidence tools, LLM integration, structured AI output, confidence, explanation |
| **Phase 8** | LangGraph | Agent state, nodes, edges, tool calls, investigation workflow |
| **Later** | Enterprise hardening | Human approval UI, authentication/RBAC, reporting, Razorpay integration, Celery/Redis, deployment, observability |

Each phase builds strictly on the previous one; no phase requires discarding work from an earlier phase.

---

## 36. What Is Implemented Now

As of this document, **no application code has been written** — this document itself is the current deliverable. "Implemented" status will be tracked here and updated as phases complete. Until updated, assume:

- Frontend: **Not started (Phase 1).**
- Backend: **Not started (Phase 1).**
- Database: **Not started (Phase 2).**
- Reconciliation engine: **Not started (Phase 5).**
- AI/LangGraph: **Not started (Phase 7–8).**
- Auth, Celery/Redis, Razorpay integration: **Not started (Later phases).**

---

## 37. What Will Be Added Later

- JWT authentication and role-based authorization.
- Live Razorpay API integration via the adapter layer.
- Celery + Redis background/scheduled processing.
- Full reporting suite (CSV/PDF export, dashboard metrics).
- Observability stack (structured logging, monitoring, alerting).
- Containerization (Docker) for consistent deployment.
- Enterprise hardening: rate limiting, read replicas, multi-instance deployment, as scale requires.

All of the above are **planned**, not implemented, as of this document.

---

## 38. Architecture Decisions

For each decision: **Decision**, **Reason**, **Tradeoff**, **Alternative considered**. Where the project specification did not state a reason explicitly, the explanation below is labeled as an **architectural rationale**, not a stated project requirement.

**Why React?**
Decision: Use React for the frontend.
Reason: Component-based UI matches the need for reusable dashboard/table/chart elements. *(Architectural rationale.)*
Tradeoff: Requires a build step and JS tooling versus a simpler server-rendered UI.
Alternative considered: Server-rendered templates (not chosen — less suited to an interactive dashboard).

**Why Vite?**
Decision: Use Vite as the frontend build tool.
Reason: Fast dev server and build times relative to older bundlers. *(Architectural rationale.)*
Tradeoff: Smaller ecosystem than some older tools, though this has narrowed significantly.
Alternative considered: Create React App (largely unmaintained), webpack directly (more configuration overhead).

**Why FastAPI?**
Decision: Use FastAPI for the backend API.
Reason: Async-capable Python framework with built-in request/response validation via Pydantic. *(Stated in project stack.)*
Tradeoff: Python async ecosystem requires care around blocking calls (e.g., Pandas operations).
Alternative considered: Flask/Django REST Framework (less native async support and validation).

**Why Python?**
Decision: Use Python for the backend.
Reason: Strong data-processing ecosystem (Pandas), and the language specified for this project. *(Stated in project stack.)*
Tradeoff: Not the fastest language at raw compute; acceptable given the I/O-bound, data-processing nature of this workload.
Alternative considered: Node.js/TypeScript (weaker data-processing tooling for this use case).

**Why PostgreSQL?**
Decision: Use PostgreSQL as the database.
Reason: Strong ACID guarantees and relational modeling suited to financial data. *(Stated in project stack; rationale is architectural.)*
Tradeoff: Requires more operational setup than a file-based database.
Alternative considered: MySQL (viable alternative; PostgreSQL chosen for its stronger feature set around constraints and data types).

**Why SQLAlchemy?**
Decision: Use SQLAlchemy as the ORM.
Reason: Mature, widely used Python ORM with strong PostgreSQL support. *(Architectural rationale.)*
Tradeoff: Adds an abstraction layer over raw SQL.
Alternative considered: Raw SQL/psycopg2 (more control, less safety and productivity).

**Why Alembic?**
Decision: Use Alembic for migrations.
Reason: Standard migration tool that pairs directly with SQLAlchemy. *(Architectural rationale.)*
Tradeoff: Requires discipline to keep migrations linear and reviewed.
Alternative considered: Manual schema scripts (rejected — not version-controlled or repeatable).

**Why Pandas?**
Decision: Use Pandas for CSV/data processing.
Reason: Standard, capable tool for tabular data cleaning and transformation. *(Stated in project stack.)*
Tradeoff: In-memory processing; not suited to very large datasets without additional strategies.
Alternative considered: Manual CSV parsing (more error-prone, less capable).

**Why LangGraph?**
Decision: Use LangGraph to orchestrate the AI investigation.
Reason: Provides explicit state, branching, and resumability for a multi-step agent workflow. *(Stated in project stack; rationale is architectural.)*
Tradeoff: Adds a framework dependency and a learning curve versus hand-rolled prompt chaining.
Alternative considered: Manually chained LLM calls without a graph framework (harder to reason about state and branching).

**Why deterministic reconciliation?**
Decision: Matching/differences are computed by code, not the LLM.
Reason: Financial correctness requires reproducible, auditable, non-probabilistic computation. *(Stated as a core project principle.)*
Tradeoff: None significant — this is treated as non-negotiable for this domain.
Alternative considered: LLM-computed matching (rejected — not reliable or auditable enough for financial facts).

**Why AI only for investigation?**
Decision: The LLM explains exceptions; it does not decide outcomes.
Reason: Keeps a human accountable for financial decisions while still benefiting from AI-assisted triage. *(Stated as a core project principle.)*
Tradeoff: Slower resolution than full automation, in exchange for safety and auditability.
Alternative considered: Fully autonomous AI resolution (rejected — inappropriate risk for financial records).

**Why CSV first?**
Decision: Start with CSV/synthetic data before live Razorpay integration.
Reason: Enables building and testing the reconciliation engine without external dependencies or credentials. *(Architectural rationale.)*
Tradeoff: CSV workflow will need to coexist with, or be replaced by, live ingestion later.
Alternative considered: Building the Razorpay integration first (rejected — adds external dependency risk early).

**Why Razorpay through an adapter?**
Decision: Access Razorpay only through a dedicated adapter layer.
Reason: Isolates the reconciliation engine from gateway-specific details, and improves testability. *(Architectural rationale.)*
Tradeoff: Slightly more upfront structure than calling the API directly.
Alternative considered: Direct API calls from the reconciliation engine (rejected — couples core logic to a specific vendor).

**Why Celery/Redis later?**
Decision: Defer Celery/Redis until background/scheduled processing is actually needed.
Reason: The MVP's reconciliation workload does not require async job infrastructure. *(Architectural rationale.)*
Tradeoff: Synchronous processing in the MVP will need to be revisited once volume or scheduling needs grow.
Alternative considered: Introducing Celery/Redis from day one (rejected as premature complexity for the MVP).

---

## 39. Technology Decision Table

| Layer | Technology | Responsibility | Why |
|---|---|---|---|
| Frontend | React | UI | Component-based UI |
| Build | Vite | Frontend development/build | Fast development |
| Styling | Tailwind CSS | UI styling | Consistent design |
| Charts | Recharts | Financial visualization | React-native charting |
| Backend | FastAPI | REST API | Python async API framework |
| Database | PostgreSQL | Persistent storage | Relational financial data |
| ORM | SQLAlchemy | Database access | Python ORM |
| Migration | Alembic | Schema migrations | Version-controlled schema |
| Data | Pandas | CSV/data processing | Data normalization |
| AI | LLM | Investigation/explanation | Reasoning over evidence |
| Agent | LangGraph | Agent workflow | State/branching/tool workflows |
| Auth | JWT | Authentication | Stateless API authentication |
| Jobs | Celery | Background jobs | Async scheduled processing |
| Queue/Broker | Redis | Job broker/cache | Background processing |
| Integration | Razorpay API | Payment data | Real payment/settlement source |

---

## 40. MVP vs Enterprise

| Capability | MVP | Enterprise/Later |
|---|---|---|
| React | Yes | Yes |
| FastAPI | Yes | Yes |
| PostgreSQL | Yes | Yes |
| CSV | Yes | Yes |
| Razorpay | Later | Yes |
| AI | Later | Yes |
| LangGraph | Later | Yes |
| JWT | Later | Yes |
| Celery | Optional | Yes |
| Redis | Optional | Yes |
| Docker | Optional | Yes |

**Important clarification:** "enterprise-level architecture" describes the *design's ability to support* these capabilities cleanly as they're added — it does not mean every technology must be implemented immediately. The MVP deliberately implements only the core loop (React + FastAPI + PostgreSQL + CSV + deterministic reconciliation) so the system is useful and testable before AI, auth, and background processing are layered on.

---

## 41. Definition of Done

A phase (or the project) is considered done against this document when:

- [ ] Every technology in use has a clearly defined responsibility.
- [ ] Connections between components are documented (who, how, what data, why).
- [ ] Data flows are documented end to end.
- [ ] Frontend/backend communication is documented.
- [ ] Backend/database communication is documented.
- [ ] AI boundaries (recommendation vs. fact) are documented and enforced in code.
- [ ] LangGraph's role is documented and does not overlap with the reconciliation engine.
- [ ] Razorpay integration goes through the adapter boundary.
- [ ] Authentication architecture is documented (even if not yet implemented).
- [ ] Celery/Redis roles are documented (even if not yet implemented).
- [ ] Security practices in [Section 29](#29-security-architecture) are followed.
- [ ] Phase progression matches [Section 35](#35-phase-by-phase-architecture-evolution).
- [ ] MVP vs. enterprise status matches [Section 40](#40-mvp-vs-enterprise).
- [ ] No unimplemented functionality is described as currently implemented.
- [ ] Deterministic financial computation and AI reasoning remain clearly separated in both design and code.
- [ ] This document remains usable as a technical reference for future developers and AI coding agents.

---

## 42. Final Architecture Diagram

```
                                   USER
                                    ↓
                             React + Vite
                                    ↓
                              API Client
                                    ↓
                             FastAPI Backend
                                    ↓
                    Authentication / Authorization (planned)
                                    ↓
                                Services
                ┌───────────────────┼────────────────────┬──────────────┐
                ↓                   ↓                    ↓              ↓
     Reconciliation Service   Exception Service   Reporting Service   Agent Service
                ↓                                                       ↓
          (deterministic)                                          LangGraph
                                                                        ↓
                                                                  Agent Tools
                                                                        ↓
                                                                       LLM
                │                                                       │
                └───────────────────────┬───────────────────────────────┘
                                         ↓
                                    PostgreSQL
                                (single source of truth)
                                         ↑
                    ┌────────────────────┴────────────────────┐
                    ↓                                          ↓
              CSV → Ingestion → Normalization        Razorpay → Adapter → Normalization
                  (current)                                  (planned)

                          Celery / Redis (planned)
                    orbit around background processing:
              scheduled reconciliation runs, batch AI investigation,
                        report generation, notifications
```

**End of document.**

# ReconAgent
## AI Finance Controller — Multi-Source Reconciliation & Exception Intelligence

**Document Type:** Master Technical & Product Specification (PRD + SRS + System Design + AI Agent Design + Implementation Roadmap)
**Status:** Draft v1.0 — Hackathon Master Blueprint
**Prepared for:** Razorpay AI Builder / AI Finance Controller Hackathon

> **Architecture note (post-implementation):** ReconAgent operates on a normalized internal financial data model. Synthetic data is provided for deterministic demos and evaluation. External payment-provider ingestion is outside the core controller — the exploratory provider integration described below was removed from the codebase, and this document is retained as the original hackathon blueprint.

---

### How to read this document — Source Labeling Convention

Every non-obvious claim in this document is tagged so readers can distinguish fact from proposal:

| Tag | Meaning |
|---|---|
| **[Source-Derived]** | Based on information explicitly provided by the user or hackathon material available in this conversation |
| **[Proposed]** | Our team's proposed solution/design — not a claim about Razorpay or the hackathon |
| **[Assumption]** | A working assumption made to unblock design; must be validated |
| **[To Be Verified]** | Needs confirmation against official Razorpay documentation, hackathon rules, or sandbox access before being treated as fact |
| **[Future Enhancement]** | Explicitly out of scope for the MVP; describes a post-hackathon production direction |

> **Important note on Razorpay claims:** No Razorpay-specific research document (e.g., "Razorpay blogs + Track 4" research) was supplied as context for this draft. Wherever this document discusses Razorpay's actual product capabilities, those statements are marked **[To Be Verified]** rather than asserted as fact. This document does **not** claim Razorpay lacks reconciliation capability — it treats Razorpay-native capability as unknown/unconfirmed and positions ReconAgent as an **additional multi-source investigation and exception-intelligence layer** that sits alongside whatever Razorpay already provides. If you supply the research file, this section and all downstream Razorpay references should be revised against it before the hackathon submission.

---

## Table of Contents

1. Document Objective
2. Executive Summary
3. Problem Statement
4. Why This Problem Matters
5. Razorpay Context
6. Existing Solutions / Gap Analysis
7. Solution Statement
8. Core Use Case
9. End-to-End Business Flow
10. System Architecture
11. Data Architecture
12. Synthetic Data Generation Strategy
13. Reconciliation Engine
14. AI Agent Design
15. AI vs Deterministic Logic
16. Human-in-the-Loop
17. Confidence Scoring
18. Exception Classification
19. Database Design
20. API Design
21. Frontend / Dashboard
22. Match Rate and Hackathon Metrics
23. Sample End-to-End Examples
24. Security
25. AI Safety / Financial Safety
26. Observability
27. Error Handling
28. Implementation Phases
29. Team Task Division
30. Testing Strategy
31. AI Evaluation
32. Demo Scenario
33. Before vs After
34. Production Architecture
35. Scalability
36. Cost Optimization
37. API / Data Integration Strategy
38. Project Directory Structure
39. Environment Variables
40. API Response Examples
41. Audit Trail
42. Known Limitations
43. Future Enhancements
44. Risks and Mitigations
45. Requirements Traceability
46. Definition of Done
47. Final Project Summary
48. Important Architectural Principles

---

## 1. Document Objective

This document is the single source of truth for the ReconAgent project. It exists to:

- Give every team member (and every judge/mentor) a shared, unambiguous understanding of the problem, the solution, and the boundaries of the MVP
- Serve as the architecture and design reference during implementation
- Provide a phase-by-phase build plan that maps directly to a 4-person hackathon team
- Separate **what is proven/required**, **what we are proposing**, and **what is explicitly future work**, so nobody over-claims to judges
- Define exactly how success will be measured (match rate, exceptions, resolutions) against the 50+ record synthetic dataset requirement **[Source-Derived]**

This is a build document, not marketing copy. Every section is written to be actionable by an engineer without further clarification.

---

## 2. Executive Summary

**ReconAgent** is an AI-powered finance operations system **[Proposed]** that automatically reconciles financial transactions across three sources — payment/settlement data, bank statement data, and internal company ledger data — using deterministic matching logic, then uses an AI agent to investigate, explain, and recommend resolutions for the transactions that don't match cleanly.

**Who has the problem:** Finance and accounting teams at any business that accepts digital payments and must periodically confirm that (a) what the payment processor says was collected, (b) what actually landed in the bank, and (c) what the business's own books expect, all agree with each other.

**What the problem is:** This three-way comparison is currently done manually — in spreadsheets, by eyeballing amounts, chasing down fees, refunds, timing gaps, and duplicates — which is slow, error-prone, and produces no structured audit trail of *why* a mismatch happened.

**Why it matters:** Manual reconciliation doesn't scale with transaction volume, delays financial close, hides recurring root causes, and creates compliance/audit risk because explanations live in someone's head or an email thread, not in a system of record.

**What our solution does:**
- Ingests three datasets (payment/settlement, bank, ledger) **[Proposed]**
- Runs **deterministic** matching logic (exact ID match, amount + date window, tolerance rules) to compute a match rate and flag anything that doesn't cleanly match — this is ordinary application code, not AI **[Proposed]**
- For every unmatched record (an "exception"), an **AI agent** gathers the related evidence across all three sources, classifies the likely root cause (fee, refund, duplicate, missing record, timing, etc.), explains its reasoning in plain language, and proposes a resolution with a transparent confidence score **[Proposed]**
- A **human finance controller** reviews the AI's evidence and recommendation and explicitly approves, rejects, or escalates it — the AI never silently changes a financial record **[Proposed]**
- Every step (match, exception, investigation, recommendation, human decision, resolution) is written to an audit trail **[Proposed]**

**Where AI is used:** Only on the subset of records that fail deterministic matching — investigation, root-cause reasoning, natural-language explanation, and recommendation generation.

**Where deterministic logic is used:** All financial arithmetic — matching, amount differences, duplicate detection by ID, tolerance checks — is plain, testable, auditable code. The AI never computes a financial number.

**What makes the system agentic:** The AI doesn't just classify a mismatch in one shot — it operates as a multi-step agent with tools (`get_bank_record`, `check_refund`, `find_related_transactions`, etc.) that it calls to gather evidence *before* reasoning, following an explicit state machine (Load Exception → Collect Evidence → Analyze → Classify → Score Confidence → Recommend → Human Review → Resolve → Verify), rather than pattern-matching from a single prompt.

**What the measurable output is:** For a 50+ record synthetic dataset **[Source-Derived requirement]**, the system reports: total records, match rate, count auto-resolved, count of exceptions, count sent to human review, count unresolved, and a stated reason/classification for every exception.

### The 30-Second Explanation (for judges)

> "Finance teams get paid data from three places — the payment processor, the bank, and their own books — and those three numbers frequently don't agree. Today someone manually compares spreadsheets to figure out why. ReconAgent automates the matching with plain deterministic code — fast, auditable, no AI guessing at math — and then hands only the *mismatches* to an AI agent that investigates like a junior analyst would: gathering evidence, explaining the likely cause, and recommending a fix — but a human always has to approve before anything is marked resolved. Every step is logged, so you get both speed and a full audit trail."

---

## 3. Problem Statement

### 3.1 The Current Workflow

```
Payment occurs
  → Payment/settlement system records the transaction
  → Funds are settled to the business's bank account
  → The business's internal ledger/invoice/order system records what it expects
  → Finance team manually pulls all three records
  → Finance team compares amounts, dates, and references line by line
  → Differences are found
  → Finance manually investigates each difference (checking fee schedules, refund logs, timing, duplicate entries)
  → Finance determines the probable root cause
  → Finance resolves/annotates the transaction
  → An audit record (if any) is maintained, often informally (email, spreadsheet comments, tickets)
```

**[Proposed / conceptual model]** — this is our team's model of the typical finance reconciliation workflow, not a Razorpay-specific process description.

### 3.2 Why This Is Difficult at Scale

- The three sources rarely use identical identifiers, so matching itself is non-trivial (reference IDs, timing, and formats differ)
- A single "mismatch" can have many different valid causes, and the correct response differs per cause
- The volume of transactions grows faster than the finance team's headcount
- Investigation requires cross-referencing multiple systems, which is manual and slow
- Explanations for *why* something didn't match are rarely written down in a structured, queryable way
- The same categories of mismatch recur, but there's no systematic capture of "we've seen this pattern before"

### 3.3 Reconciliation Scenarios Addressed (Proposed Scope)

These are the mismatch categories ReconAgent is designed to detect and reason about. They are **[Proposed]** reconciliation scenarios based on general finance-operations reasoning, not a confirmed list of what Razorpay specifically supports or documents:

| Scenario | Description |
|---|---|
| Processing/payment fees | Net settled amount is less than gross amount by a fee |
| Settlement differences | Amount settled to bank differs from amount recorded at payment time |
| Missing transactions | A record exists in one source but not another |
| Duplicate transactions | The same transaction appears more than once in a source |
| Refunds | A payment was later reversed, fully or partially |
| Partial payments | Only part of the expected amount was received |
| Timing differences | Transaction dates differ across sources due to settlement lag |
| Tax/TDS-related differences | A deduction reduces the settled amount **[Assumption — applicability depends on jurisdiction/business type, To Be Verified]** |
| Incorrect amounts | A genuine data-entry or processing error |
| Missing ledger records | Business never recorded an invoice/order for a payment that exists |
| Missing bank records | Payment/ledger show a transaction the bank statement doesn't have (settlement delay or failure) |
| Unexpected adjustments | Chargebacks, corrections, or manual adjustments with no clear source record |

We explicitly do **not** claim that all of these are documented Razorpay behaviors — they are the scenario space our synthetic dataset is designed to cover, framed generically as "financial reconciliation exception types."

---

## 4. Why This Problem Matters

**[Proposed reasoning — general finance-operations impact, not sourced from Razorpay-specific data]**

- **Manual finance effort**: Every mismatch currently consumes analyst time that scales linearly (or worse) with transaction volume.
- **Delayed reconciliation**: Financial close and reporting are gated on manual investigation finishing.
- **Operational overhead**: Cross-referencing three systems by hand is repetitive and interruption-prone work.
- **Human error**: Manual comparison of many similar-looking rows is exactly the kind of task humans get wrong at scale.
- **Slow exception resolution**: Without a structured queue, exceptions can sit unresolved for extended periods.
- **Lack of explainability**: Even when someone resolves a mismatch, the reasoning is rarely captured in a reusable, structured form.
- **Audit/compliance concerns**: Auditors want to see *why* a discrepancy was closed, not just that it was closed.
- **Cash visibility problems**: Unreconciled amounts create uncertainty about actual available cash.
- **No pattern recognition**: The same root cause (e.g., a recurring fee structure) may be re-investigated from scratch every time instead of being recognized.

**Why a finance controller needs more than a dashboard:** A dashboard shows *that* numbers don't match. It doesn't investigate *why*, gather the supporting evidence, or draft a defensible explanation. ReconAgent's differentiator is doing the investigative legwork and producing an evidence-backed recommendation — while keeping the human as the final decision-maker.

---

## 5. Razorpay Context

> Everything Razorpay-specific in this section is marked **[To Be Verified]**. No Razorpay product documentation or research file was provided in this conversation. Do not present these as confirmed facts to judges without verifying against actual Razorpay documentation.

### 5.1 Conceptual Payment Flow **[To Be Verified]**

```
Customer
  → Chooses a payment method (card, UPI, netbanking, wallet, etc.)
  → Payment is processed through Razorpay's payment infrastructure
  → Razorpay processes/authorizes the payment
  → Funds are settled from Razorpay to the merchant/business's bank account
  → Business records this transaction as revenue/income in its internal ledger
```

This is a generic description of how payment aggregators/processors typically operate **[Assumption]**, not a confirmed description of Razorpay's specific product architecture, APIs, or settlement mechanics.

### 5.2 Roles of Each Record Type in Our Architecture **[Proposed]**

| Record Type | Role in ReconAgent |
|---|---|
| Payment/settlement records | Represents what the payment processor reports was charged and settled, including any fees deducted |
| Bank statement records | Represents what actually arrived in the business's bank account — ground truth for cash |
| Internal ledger/invoice records | Represents what the business itself expected to receive, based on its own orders/invoices |

### 5.3 Explicit Scope Boundary

- **Razorpay-related payment/settlement data is treated as ONE of three input sources** in our architecture — not the sole data source, and not something our system claims exclusive ownership or expertise over.
- **We do not claim** to know or replicate Razorpay's internal reconciliation logic, fee schedules, settlement cycles, or APIs beyond what is publicly documented and verified.
- **We do claim** to build a source-agnostic reconciliation and exception-intelligence layer that *can* consume Razorpay-shaped payment/settlement data (via CSV in the MVP, via API in a future integration) alongside bank and ledger data.

### 5.4 "Razorpay's Existing Capabilities" vs "Our Proposed Intelligence Layer"

| | Razorpay's Existing Capabilities | ReconAgent's Proposed Layer |
|---|---|---|
| Status | **[To Be Verified]** — not confirmed in this document | **[Proposed]** — this is what we are building |
| Scope | Whatever Razorpay natively offers for payment processing/settlement reporting (unconfirmed here) | Multi-source (payment + bank + ledger) matching, exception detection, AI-driven root-cause investigation, human approval workflow, and audit trail |
| Our claim | We make no claim about what Razorpay does or doesn't already do | We claim to add a cross-source investigation and explanation layer that a single-source payment dashboard would not natively provide |

---

## 6. Existing Solutions / Gap Analysis

| Existing Approach | What It Does | Limitation | Gap |
|---|---|---|---|
| Traditional spreadsheet reconciliation | Manual VLOOKUP/pivot-based comparison across exported CSVs | Slow, error-prone, no audit trail, doesn't scale | No automation, no explanation, no history |
| Manual finance operations | Analysts manually chase down each mismatch | High labor cost, inconsistent investigation quality | No structured evidence capture, no reuse of past reasoning |
| Basic accounting software reconciliation (e.g., generic ledger tools) | Matches transactions by ID/amount within one or two sources **[Assumption]** | Typically single-source-pair matching; limited root-cause explanation | Doesn't natively span payment + bank + ledger as three linked sources with AI-explained exceptions |
| Rule-based reconciliation engines | Deterministic matching with fixed rules/tolerances | Rules can match/flag but can't *explain why* a flagged item failed, and can't reason about novel causes | No natural-language, evidence-grounded investigation |
| Razorpay-native reconciliation capabilities | **[To Be Verified — unconfirmed in this document]** | **[To Be Verified]** | We do not assert a specific gap here without verified source material; ReconAgent is positioned as complementary, not a replacement |
| Generic AI assistants (e.g., asking an LLM to "explain this discrepancy" ad hoc) | Can produce plausible-sounding text about a discrepancy | Not grounded in actual retrieved records; no tool use; no deterministic backing; no audit trail; prone to fabrication | Lacks evidence-grounding, structured tool access, and auditability |
| AI agents (general purpose, non-finance-specific) | Can call tools and reason over multiple steps | Not purpose-built for finance's need for deterministic math + human approval + audit logging | Lacks the deterministic/AI separation finance requires |

### THE GAP

The gap ReconAgent targets is the **combination**, not any single capability in isolation:

1. **Multi-source reconciliation** across payment, bank, and ledger data simultaneously (not just one pairwise comparison)
2. **Deterministic, auditable matching** as the foundation — not AI-driven arithmetic
3. **Automated exception detection** that flags precisely what didn't match and why it's an exception
4. **AI-driven investigation** that gathers cross-source evidence before forming an explanation
5. **Root-cause reasoning** that classifies exceptions into a structured taxonomy, not free-text guesses
6. **Evidence-based explanations** — every AI claim is traceable to specific retrieved records
7. **Transparent confidence scoring** based on named signals, not an opaque LLM-invented number
8. **Human approval** as a mandatory gate before any resolution is considered final
9. **Resolution tracking** so outcomes feed back into a structured record
10. **Full auditability** of every step from ingestion to resolution

No single category above (spreadsheets, rule engines, generic AI, or — pending verification — Razorpay's own tooling) is claimed to cover all ten of these simultaneously. That combination is ReconAgent's proposed contribution.

---

## 7. Solution Statement

> **ReconAgent is an AI-powered finance operations system that automatically reconciles financial records across multiple sources, detects exceptions, investigates their probable causes using structured evidence and AI reasoning, recommends resolutions, and routes uncertain cases to a human finance controller for approval before anything is marked resolved.** **[Proposed]**

Expanded:

ReconAgent ingests three structured datasets — payment/settlement records, bank statement records, and internal ledger records — and runs them through a deterministic matching engine that establishes, with full transparency, which records agree across all three sources within defined tolerances. Every record that does *not* cleanly match becomes a tracked **exception**. For each exception, a LangGraph-orchestrated AI agent collects the relevant evidence from all three sources using a constrained set of read-only tools, classifies the likely root cause against a fixed taxonomy (fee, refund, duplicate, missing record, timing difference, etc.), generates a plain-language explanation grounded in the retrieved evidence, and proposes a recommended action with an evidence-based confidence score. This recommendation is queued for a human finance controller, who can approve, reject, request further investigation, or mark the case unresolved. Every action across the entire pipeline — ingestion, matching, exception creation, AI investigation, recommendation, human decision, and resolution — is written to an immutable audit log, so the system produces not just faster reconciliation but a defensible, queryable record of *why* every decision was made.

---

## 8. Core Use Case

### Primary Use Case: Multi-Source Payment / Settlement Reconciliation

**Sources:**

1. **Payment/Settlement dataset** — what the payment processor recorded and settled **[Proposed as generic "payment/settlement" source; Razorpay-specific field names to be verified]**
2. **Bank statement dataset** — what actually arrived in the business's bank account
3. **Internal ledger/invoice/order dataset** — what the business expected/recorded on its own books

| Source | Represents | Example |
|---|---|---|
| Payment/Settlement | What the payment system recorded as charged/settled, net of fees | ₹10,000 gross, ₹9,700 net settled |
| Bank | What actually reached the bank account | ₹9,700 |
| Internal Ledger | What the business expected to receive per its own invoice/order | ₹10,000 |

In this example, the deterministic engine would compute a ₹300 difference between the ledger's expected amount and the bank's actual amount, and the AI agent's job is to determine — using the payment/settlement record's fee field as evidence — that this is most likely a **PROCESSING_FEE** exception, not a missing payment or an error.

---

## 9. End-to-End Business Flow

### 9.1 High-Level Flow

```mermaid
flowchart TD
    A[Financial Data Sources] --> B[Data Ingestion]
    B --> C[Normalization]
    C --> D[Deterministic Matching]
    D --> E{Matched?}
    E -->|Yes| F[Mark Reconciled]
    E -->|No| G[Create Exception]
    G --> H[AI Investigation]
    H --> I[Root Cause Analysis]
    I --> J[Recommendation + Confidence Score]
    J --> K[Human Approval Queue]
    K -->|Approve| L[Resolve Record]
    K -->|Reject| M[Return to Exception Queue]
    K -->|Escalate| N[Mark Needs Deeper Review]
    L --> O[Verification]
    O --> P[Audit Trail]
    F --> P
    M --> P
    N --> P
```

### 9.2 Data Flow Diagram

```mermaid
flowchart LR
    subgraph Sources
        S1[Payment/Settlement CSV]
        S2[Bank Statement CSV]
        S3[Internal Ledger CSV]
    end
    S1 --> ING[Ingestion Service]
    S2 --> ING
    S3 --> ING
    ING --> NORM[Normalization Layer]
    NORM --> DB[(PostgreSQL)]
    DB --> MATCH[Matching Engine]
    MATCH --> DB
    DB --> AGENT[AI Investigation Agent]
    AGENT --> DB
    DB --> API[FastAPI Layer]
    API --> UI[React Dashboard]
    UI --> API
    API --> DB
```

### 9.3 Agent Workflow

```mermaid
flowchart TD
    START([Start]) --> LOAD[Load Exception]
    LOAD --> COLLECT[Collect Evidence via Tools]
    COLLECT --> ANALYZE[Analyze Evidence]
    ANALYZE --> CLASSIFY[Classify Root Cause]
    CLASSIFY --> CONF[Calculate Confidence]
    CONF --> REC[Generate Recommendation]
    REC --> HUMAN[Queue for Human Review]
    HUMAN --> END([End of Agent Turn])
```

### 9.4 Exception Workflow

```mermaid
flowchart TD
    E[Exception Created] --> T{Exception Type Known?}
    T -->|No| INV[AI Investigation]
    T -->|Yes, historical pattern match| FAST[Fast-Path Classification]
    INV --> CONF{Confidence >= Threshold?}
    FAST --> CONF
    CONF -->|High| AUTO[Recommend Auto-Approve Path]
    CONF -->|Low/Medium| REVIEW[Route to Human Review]
    AUTO --> HUMAN[Human Confirms — never fully automatic]
    REVIEW --> HUMAN
    HUMAN --> RESOLVE[Resolved]
    HUMAN --> UNRESOLVED[Unresolved]
```

> **[Proposed principle]**: Even "high confidence" recommendations still require human confirmation in the MVP (see Section 16). There is no fully-automatic silent resolution path for financial records.

### 9.5 Human-in-the-Loop Workflow

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant Queue as Review Queue
    participant Human as Finance Controller
    participant DB as Database
    Agent->>Queue: Submit recommendation + evidence + confidence
    Queue->>Human: Present exception with AI findings
    Human->>Human: Review evidence, explanation, confidence
    alt Approve
        Human->>DB: Approve resolution
        DB->>DB: Mark record RESOLVED
    else Reject
        Human->>DB: Reject, add note
        DB->>DB: Return to exception queue
    else Investigate Further
        Human->>Agent: Request additional evidence
        Agent->>Queue: Resubmit with new evidence
    else Mark Unresolved
        Human->>DB: Flag UNRESOLVED
    end
    DB->>DB: Write audit log entry
```

### 9.6 System Architecture Diagram

```mermaid
flowchart TB
    subgraph Client
        FE[React + Vite + Tailwind Dashboard]
    end
    subgraph Backend
        API[FastAPI REST API]
        AUTH[Auth Service - JWT]
        MATCH[Deterministic Matching Engine - Pandas]
        AGENT[LangGraph AI Agent]
        TOOLS[Agent Tools Layer]
    end
    subgraph Data
        PG[(PostgreSQL)]
        FILES[CSV Upload Storage]
    end
    subgraph AI
        LLM[LLM Provider - Structured Output / Tool Calling]
    end
    FE -->|HTTPS/JWT| API
    API --> AUTH
    API --> MATCH
    API --> AGENT
    AGENT --> TOOLS
    TOOLS --> PG
    AGENT --> LLM
    MATCH --> PG
    API --> PG
    API --> FILES
```

### 9.7 Deployment Architecture (MVP)

```mermaid
flowchart TB
    subgraph "Local / Hackathon Deployment"
        FE2[Frontend - Vite dev server / static build]
        BE2[Backend - FastAPI via Uvicorn]
        DB2[(PostgreSQL - local or managed instance)]
    end
    FE2 -->|REST/JSON| BE2
    BE2 -->|SQL| DB2
    BE2 -->|API call| LLMAPI[LLM API Provider]
```

### 9.8 Database Relationship Diagram

See Section 19 for the full ER diagram.

### 9.9 API Flow (Example: Running a Reconciliation)

```mermaid
sequenceDiagram
    participant UI
    participant API
    participant Match as Matching Engine
    participant DB

    UI->>API: POST /api/reconciliation/upload (3 CSVs)
    API->>DB: Store raw records
    UI->>API: POST /api/reconciliation/run
    API->>Match: Execute deterministic matching
    Match->>DB: Write match results + exceptions
    API-->>UI: 202 Accepted { run_id }
    UI->>API: GET /api/reconciliation/{run_id}
    API->>DB: Fetch run summary
    API-->>UI: 200 OK { match_rate, exceptions, ... }
```

---

## 10. System Architecture

### 10.1 Recommended Stack and Rationale

| Layer | Technology | Why |
|---|---|---|
| Frontend | React + Vite | Fast dev iteration for a hackathon timeline; component reuse across Dashboard/Queue/Detail views |
| Styling | Tailwind CSS | Rapid, consistent UI styling without a heavy design system |
| Charts | Recharts | Native React charting for match-rate/KPI visualizations without extra complexity |
| Backend | Python + FastAPI | Async-capable, auto-generates OpenAPI docs (useful for judges/demo), strong typing via Pydantic matches our need for structured AI outputs |
| Database | PostgreSQL + SQLAlchemy | Relational integrity needed for linked payment/bank/ledger/exception records; SQLAlchemy gives us migrations and ORM safety |
| Data processing | Pandas | Standard, well-understood tool for CSV ingestion, normalization, and the deterministic matching logic |
| AI | LLM with structured outputs / tool calling | Needed so agent outputs are parseable and tool calls are constrained, rather than free-text |
| Agent orchestration | LangGraph | Gives us an explicit, inspectable state machine for the agent (Load → Collect → Analyze → Classify → Recommend), rather than a single uncontrolled prompt chain |
| Auth | JWT | Simple, stateless auth appropriate for a demo-scale app with a small number of finance-controller users |
| Optional: Redis | Caching repeated agent investigations / rate-limiting | Only if time permits — not MVP-critical |
| Optional: Celery | Background processing of large reconciliation runs | Only relevant at higher volume than the 50-record hackathon dataset; documented for production, not required for MVP |
| Optional: Docker | Containerized local dev / demo reproducibility | Recommended if team has bandwidth; not required to satisfy the MVP scope |

We do **not** add a message queue, Kubernetes, or a separate microservice mesh for the MVP — at 50–100 synthetic records, a single FastAPI service is sufficient and keeps the hackathon build tractable (see Section 48, Principle: "MVP architecture must remain simple enough to implement during the hackathon").

### 10.2 Component Responsibilities

- **Frontend**: Upload UI, dashboard KPIs, exception queue, exception detail (evidence + AI explanation), approval actions, audit trail viewer
- **API layer**: Auth, CRUD over runs/exceptions/approvals, triggers matching engine and agent runs
- **Matching engine**: Pure deterministic Python/Pandas logic; no LLM calls
- **Agent layer**: LangGraph state machine; only invoked per-exception, not per-record
- **Database**: System of record for all input data, match results, exceptions, investigations, recommendations, approvals, and audit logs

---

## 11. Data Architecture

### 11.1 Data Source Strategy for the MVP

**For the MVP, ReconAgent uses SYNTHETIC DATA.** **[Proposed]** Synthetic data is used for development and demonstration unless authorized Razorpay sandbox/API access is confirmed available **[To Be Verified]** — in which case sandbox data may supplement, but should not replace, the synthetic dataset designed to guarantee coverage of every required exception scenario.

Three datasets are produced:

- `payment_settlement.csv`
- `bank_statement.csv`
- `internal_ledger.csv`

**Target: 50+ records total across the combined dataset**, sufficient to demonstrate matches, every exception category in scope, and human-review cases, per the hackathon requirement **[Source-Derived]**.

### 11.2 Sample Schemas

#### Payment/Settlement

| Field | Type | Required/Optional/Proposed |
|---|---|---|
| `transaction_id` | string | Required |
| `payment_id` | string | Required |
| `amount` | decimal | Required — gross amount |
| `fee` | decimal | Required (may be 0) — processing fee |
| `net_amount` | decimal | Required — amount after fee deduction |
| `currency` | string | Required (e.g., `INR`) |
| `status` | enum (`captured`, `refunded`, `failed`, `partial_refund`) | Required — Proposed enum values |
| `settlement_date` | date | Required |
| `reference_id` | string | Required — used to cross-link to ledger/bank |

#### Bank Statement

| Field | Type | Required/Optional/Proposed |
|---|---|---|
| `bank_transaction_id` | string | Required |
| `reference_id` | string | Required — used to cross-link to payment/ledger |
| `credit_amount` | decimal | Optional (present if inbound) |
| `debit_amount` | decimal | Optional (present if outbound/adjustment) |
| `transaction_date` | date | Required |
| `description` | string | Optional — free text, useful evidence for AI reasoning |

#### Internal Ledger

| Field | Type | Required/Optional/Proposed |
|---|---|---|
| `invoice_id` | string | Required |
| `transaction_id` | string | Required — links to payment/settlement record |
| `expected_amount` | decimal | Required |
| `customer_id` | string | Required |
| `invoice_date` | date | Required |
| `status` | enum (`open`, `paid`, `partially_paid`, `cancelled`) | Required — Proposed enum values |

All three schemas above are **[Proposed]** — designed by our team to support the matching and exception scenarios in scope, not copied from a confirmed Razorpay data export format.

---

## 12. Synthetic Data Generation Strategy

### 12.1 Approach

Generate all three CSVs programmatically (e.g., a single Python script using Pandas + `random`/`faker`) so that:

- Every record has a shared **transaction/reference key** threaded across the relevant sources
- A known, pre-labeled subset of records is deliberately constructed to hit each required exception scenario
- The generator writes a "ground truth" mapping (internal only, not exposed to the AI agent) of the *intended* exception type per record, so the team can measure AI classification accuracy against a known answer

### 12.2 Required Scenarios

| Scenario | Input Records | Expected Result | Expected AI Behavior |
|---|---|---|---|
| Exact match | Same amount, same reference, aligned dates across all 3 sources | Matched, no exception | N/A — never reaches the agent |
| Processing fee difference | Ledger expects gross; bank/net settlement is gross − fee | Exception: amount mismatch | Agent finds fee field in payment record, classifies `PROCESSING_FEE`, high confidence |
| Duplicate transaction | Same reference/amount appears twice in one source | Exception: duplicate flagged by matching engine | Agent confirms via `check_duplicate()`, classifies `DUPLICATE` |
| Missing bank transaction | Payment + ledger exist, no bank record | Exception: no bank match found | Agent classifies `MISSING_BANK_RECORD`, recommends "await settlement" or "investigate failed transfer" |
| Missing payment transaction | Bank + ledger exist, no payment/settlement record | Exception: no payment match found | Agent classifies `MISSING_PAYMENT_RECORD` |
| Missing ledger entry | Payment + bank exist, no internal invoice | Exception: no ledger match found | Agent classifies `MISSING_LEDGER_RECORD`, flags possible unbilled revenue |
| Refund | Payment record shows `refunded` status; bank shows offsetting debit | Exception: amount not matching original expected | Agent classifies `REFUND`, references refund status field as evidence |
| Partial payment | Bank/payment amount is less than ledger expected amount, no refund flag | Exception: amount mismatch | Agent classifies `PARTIAL_PAYMENT` |
| Timing difference | Same amount/reference but dates fall outside the matching window | Exception: date-window mismatch | Agent classifies `TIMING_DIFFERENCE`, notes settlement lag as likely cause |
| Incorrect amount | Amount differs with no fee/refund/partial explanation available in evidence | Exception: unexplained amount mismatch | Agent classifies `AMOUNT_MISMATCH`, lower confidence, recommends human investigation |
| Tax/TDS-like deduction | Net settlement reduced by a deduction beyond the stated fee **[Assumption]** | Exception: residual amount mismatch after fee accounted for | Agent classifies `TAX_DEDUCTION` if evidence supports it, otherwise defers to `UNKNOWN` |
| Unknown/unresolvable exception | Deliberately ambiguous — no supporting evidence found in any source | Exception: no matching evidence | Agent must explicitly return `UNKNOWN` rather than guessing; low confidence; routed to mandatory human review |

### 12.3 Data Generation Table Template

| Scenario | Expected Detection | Expected Explanation | Resolution |
|---|---|---|---|
| Exact match | Matched by matching engine | N/A | Auto-reconciled |
| Fee difference | Exception → `PROCESSING_FEE` | "Net settlement is lower than expected by an amount matching the recorded fee" | Recommend approve |
| Duplicate | Exception → `DUPLICATE` | "Same reference/amount appears twice in [source]" | Recommend flag second entry |
| Missing bank record | Exception → `MISSING_BANK_RECORD` | "No corresponding bank transaction found for this reference within the matching window" | Recommend await/verify settlement |
| Unknown | Exception → `UNKNOWN` | "No supporting evidence found across available sources" | Mandatory human review |

*(Full table to be completed by the team once the generator script produces the final labeled dataset.)*

---

## 13. Reconciliation Engine

### 13.1 Matching Logic (Deterministic — No LLM Involvement)

The matching engine is implemented in plain Python/Pandas. **It must never call an LLM to compute a match or a financial difference.**

Matching steps, in order:

1. **Exact transaction ID / reference ID matching** — join payment, bank, and ledger records on shared reference keys
2. **Amount matching within tolerance** — compare amounts with a configurable tolerance (e.g., ₹1 or 0.5%, to absorb rounding) **[Proposed default, to be tuned]**
3. **Date-window matching** — allow a configurable settlement lag window (e.g., ±3 days) **[Proposed default]** before treating a date difference as a timing exception
4. **Duplicate detection** — flag any reference/amount pair appearing more than once within a single source
5. **Missing-record detection** — identify references present in one or two sources but absent from the third
6. **Fuzzy/reference matching (optional, if exact reference linking is insufficient)** — e.g., matching by amount + date proximity when reference IDs are inconsistently formatted **[Proposed, secondary fallback only]**

### 13.2 Matching Confidence vs AI Confidence

These are explicitly **different, non-comparable scores**:

- **Matching confidence** (deterministic): a rule-based score (e.g., "matched on exact reference + exact amount" = 100%; "matched on amount + date window only" = 80%) computed entirely by code
- **AI confidence** (agent-generated): a signal-based score (Section 17) about how confident the agent is in its *root-cause classification* of an already-established exception

The system must never conflate "the record matched with X% confidence" with "the AI is X% confident about why it didn't match."

### 13.3 Core Calculation

```python
difference = expected_amount - received_amount
```

This calculation — and everything derived from it (match/no-match, exception creation, tolerance checks) — is performed in deterministic application code, never inferred by the LLM.

---

## 14. AI Agent Design

### 14.1 What the Agent Observes

The agent is invoked **only after** an exception has been deterministically created. It receives:

- The exception record (which source(s) it came from, the computed difference, matching metadata)
- Read-only access to related records across all three sources via tools (not raw dataset dumps)

### 14.2 What Data It Receives

- The specific payment/settlement, bank, and ledger records linked (or plausibly linked) to the exception's reference key
- Any related records surfaced by `find_related_transactions()` (e.g., a possible duplicate or a delayed match)
- It does **not** receive the full 50+ record dataset in context — only the evidence relevant to the exception under investigation (see Section 36, cost optimization)

### 14.3 Tools Available to the Agent

| Tool | Purpose |
|---|---|
| `get_transaction(reference_id)` | Retrieve a specific transaction across sources |
| `get_settlement(reference_id)` | Retrieve the payment/settlement record |
| `get_bank_record(reference_id)` | Retrieve the bank statement record |
| `get_ledger_record(reference_id)` | Retrieve the internal ledger record |
| `calculate_difference(expected, actual)` | Deterministic helper — returns a computed numeric difference (implemented in code, called by the agent, not computed by the LLM itself) |
| `find_related_transactions(reference_id)` | Search for plausible related records (e.g., possible duplicates, delayed matches) |
| `check_duplicate(reference_id)` | Deterministic duplicate check |
| `check_fee(reference_id)` | Look up the recorded fee for a transaction |
| `check_refund(reference_id)` | Look up refund status/history |
| `generate_explanation(evidence)` | LLM-generated natural-language explanation, grounded in the evidence gathered |
| `create_reconciliation_note(exception_id, note)` | Writes the agent's finding to the exception record for human review |

### 14.4 Decisions the Agent CAN Make

- Which tools to call and in what order to gather evidence
- Which root-cause category (from the fixed taxonomy) best fits the evidence
- What confidence score to assign, based on named signals (Section 17)
- What explanation text to generate
- What resolution it recommends (e.g., "approve as processing fee")

### 14.5 Decisions the Agent MUST NOT Make Autonomously

- It must **not** mark any record as финально resolved without human approval
- It must **not** modify any financial record (amount, status) directly
- It must **not** invent evidence not returned by its tools
- It must **not** assign a confidence score without being able to point to the signals that produced it
- It must **not** silently default an `UNKNOWN` case to a guessed category just to avoid returning `UNKNOWN`

### 14.6 Agent State Model (LangGraph)

```
START
 ↓
LOAD EXCEPTION
 ↓
COLLECT EVIDENCE   (tool calls: get_settlement, get_bank_record, get_ledger_record, find_related_transactions, check_duplicate, check_fee, check_refund)
 ↓
ANALYZE            (LLM reasons over collected evidence only)
 ↓
CLASSIFY ROOT CAUSE (must select from fixed taxonomy, Section 18, or UNKNOWN)
 ↓
CALCULATE CONFIDENCE (signal-based, Section 17)
 ↓
GENERATE RECOMMENDATION (generate_explanation + create_reconciliation_note)
 ↓
HUMAN REVIEW        (external to the agent — agent's turn ends here)
 ↓
RESOLVE             (triggered by human action, not the agent)
 ↓
VERIFY              (deterministic re-check that resolution is consistent)
 ↓
END
```

---

## 15. AI vs Deterministic Logic

| Responsibility | Deterministic Code | AI Agent |
|---|---|---|
| Amount calculation | YES | NO |
| Matching IDs | YES | NO |
| Difference calculation | YES | NO |
| Duplicate detection | YES | MAY ASSIST (confirms via tool, doesn't compute independently) |
| Root cause reasoning | NO | YES |
| Explanation | NO | YES |
| Recommendation | NO | YES |
| Human approval | NO | HUMAN |
| Audit logging | YES | NO |

**Why this separation is safer for finance:** Financial arithmetic must be reproducible, testable, and immune to model non-determinism or hallucination. By confining the LLM strictly to reasoning, classification, and explanation over evidence that deterministic code has already retrieved and computed, we get the benefits of AI (flexible reasoning about ambiguous, natural-language-shaped causes) without exposing the system's financial integrity to LLM error. Every number a judge or auditor sees traces back to plain application code; every *explanation* traces back to a specific, cited piece of evidence the agent retrieved via tools.

---

## 16. Human-in-the-Loop

### 16.1 Status Taxonomy

| Status | Meaning |
|---|---|
| `AUTO_MATCHED` | Deterministically matched — never enters the AI/human pipeline |
| `RECOMMENDED` | AI has produced a classification, explanation, and recommendation; awaiting human decision |
| `NEEDS_REVIEW` | Low/medium AI confidence, or agent explicitly returned `UNKNOWN`; requires closer human attention |
| `APPROVED` | Human has approved the AI's recommendation (or their own manual resolution) |
| `REJECTED` | Human disagrees with the AI's recommendation; case returns to the queue with a note |
| `UNRESOLVED` | Explicitly left open — no confident resolution exists yet |

### 16.2 Example Interaction

> **AI says:** "₹300 discrepancy is likely a processing fee, based on the recorded fee field in the payment/settlement record (₹300), which exactly matches the shortfall between the ledger's expected amount and the bank's received amount. Confidence: 96%."

> **Human can:**
> - **Approve** → status becomes `RESOLVED`, audit entry recorded with the AI's explanation attached
> - **Reject** → status returns to `NEEDS_REVIEW` with the human's note appended
> - **Investigate** → triggers the agent to gather additional evidence (e.g., check for a second, related transaction)
> - **Mark unresolved** → status becomes `UNRESOLVED`, visible on the dashboard as an open item

### 16.3 Auditability

Every human action (approve/reject/investigate/unresolved) is logged with: user ID, timestamp, the AI recommendation shown at the time of decision, and any note entered. This ensures that even a rejected AI recommendation is preserved for audit purposes, not overwritten.

---

## 17. Confidence Scoring

### 17.1 Principle

The LLM must not invent an arbitrary confidence number. Confidence is computed from **named, inspectable signals**, and the LLM's role is to report which signals were present/absent — the score itself should be computed by deterministic aggregation logic wherever possible, with the LLM contributing qualitative judgment only where a signal is inherently textual (e.g., description-field similarity).

### 17.2 Signals

| Signal | Description |
|---|---|
| Amount alignment | Does the discrepancy amount exactly (or near-exactly) match a known reference value (e.g., the fee field)? |
| Reference match | Do the reference IDs across sources align exactly, partially, or not at all? |
| Fee alignment | Does the difference match the recorded fee within tolerance? |
| Date alignment | Is the date difference within a plausible settlement-lag window? |
| Historical pattern | Has a similar pattern (same customer, same amount delta) been resolved before with a known category? |
| Supporting evidence | How many of the three sources have a record supporting the proposed explanation? |
| Conflicting records | Are there multiple candidate explanations with contradicting evidence? |

### 17.3 Interpretation

Confidence is a **decision-support signal, not proof**. It informs how the case is routed (e.g., higher confidence cases may be presented first or with a suggested "quick approve" UI affordance) but never bypasses the mandatory human approval step in the MVP.

---

## 18. Exception Classification

| Category | MVP / Optional / Future |
|---|---|
| `PROCESSING_FEE` | MVP |
| `REFUND` | MVP |
| `DUPLICATE` | MVP |
| `MISSING_BANK_RECORD` | MVP |
| `MISSING_PAYMENT_RECORD` | MVP |
| `MISSING_LEDGER_RECORD` | MVP |
| `TIMING_DIFFERENCE` | MVP |
| `PARTIAL_PAYMENT` | MVP |
| `AMOUNT_MISMATCH` | MVP |
| `TAX_DEDUCTION` | Optional — include only if the synthetic dataset models it; mark evidence source as **[Assumption]** |
| `UNKNOWN` | MVP — mandatory fallback, must always be available to the agent |
| Chargeback-specific category | Future |
| Multi-currency conversion difference | Future |
| Cross-border/FX-specific categories | Future |

---

## 19. Database Design

### 19.1 Tables

| Table | Purpose | Key Columns |
|---|---|---|
| `users` | Finance controllers / demo users | `id` (PK), `email`, `hashed_password`, `role` |
| `payments` | Raw ingested payment/settlement records | `id` (PK), `transaction_id`, `payment_id`, `amount`, `fee`, `net_amount`, `status`, `settlement_date`, `reference_id` |
| `bank_transactions` | Raw ingested bank statement records | `id` (PK), `bank_transaction_id`, `reference_id`, `credit_amount`, `debit_amount`, `transaction_date`, `description` |
| `ledger_entries` | Raw ingested internal ledger records | `id` (PK), `invoice_id`, `transaction_id`, `expected_amount`, `customer_id`, `invoice_date`, `status` |
| `reconciliation_runs` | One row per upload+match execution | `id` (PK), `run_at`, `total_records`, `matched_count`, `exception_count`, `triggered_by` (FK → `users.id`) |
| `reconciliation_results` | Per-record match outcome for a run | `id` (PK), `run_id` (FK), `reference_id`, `match_status`, `match_confidence` |
| `exceptions` | Records flagged as unmatched | `id` (PK), `run_id` (FK), `reference_id`, `difference_amount`, `status`, `created_at` |
| `agent_investigations` | AI agent's evidence-gathering + reasoning trace per exception | `id` (PK), `exception_id` (FK), `evidence_json`, `reasoning_summary`, `created_at` |
| `recommendations` | AI-generated classification + explanation + confidence | `id` (PK), `investigation_id` (FK), `category`, `explanation`, `confidence_score`, `created_at` |
| `approvals` | Human decisions on recommendations | `id` (PK), `recommendation_id` (FK), `user_id` (FK), `decision`, `note`, `decided_at` |
| `audit_logs` | Immutable append-only action log | `id` (PK), `entity_type`, `entity_id`, `action`, `actor`, `payload_json`, `timestamp` |

**Indexes (illustrative):** `reference_id` indexed on `payments`, `bank_transactions`, `ledger_entries` for join performance; `status` indexed on `exceptions`; `run_id` indexed on `reconciliation_results` and `exceptions`.

### 19.2 ER Diagram

```mermaid
erDiagram
    USERS ||--o{ RECONCILIATION_RUNS : triggers
    USERS ||--o{ APPROVALS : decides
    RECONCILIATION_RUNS ||--o{ RECONCILIATION_RESULTS : contains
    RECONCILIATION_RUNS ||--o{ EXCEPTIONS : produces
    EXCEPTIONS ||--o{ AGENT_INVESTIGATIONS : triggers
    AGENT_INVESTIGATIONS ||--o{ RECOMMENDATIONS : produces
    RECOMMENDATIONS ||--o{ APPROVALS : reviewed_by
    PAYMENTS ||--o| RECONCILIATION_RESULTS : referenced_in
    BANK_TRANSACTIONS ||--o| RECONCILIATION_RESULTS : referenced_in
    LEDGER_ENTRIES ||--o| RECONCILIATION_RESULTS : referenced_in
    EXCEPTIONS ||--o{ AUDIT_LOGS : logged_in
    APPROVALS ||--o{ AUDIT_LOGS : logged_in

    USERS {
        int id PK
        string email
        string role
    }
    PAYMENTS {
        int id PK
        string transaction_id
        string reference_id
        decimal amount
        decimal fee
        decimal net_amount
        string status
        date settlement_date
    }
    BANK_TRANSACTIONS {
        int id PK
        string reference_id
        decimal credit_amount
        decimal debit_amount
        date transaction_date
    }
    LEDGER_ENTRIES {
        int id PK
        string reference_id
        decimal expected_amount
        string customer_id
        string status
    }
    RECONCILIATION_RUNS {
        int id PK
        datetime run_at
        int total_records
        int matched_count
        int exception_count
    }
    RECONCILIATION_RESULTS {
        int id PK
        int run_id FK
        string reference_id
        string match_status
        float match_confidence
    }
    EXCEPTIONS {
        int id PK
        int run_id FK
        string reference_id
        decimal difference_amount
        string status
    }
    AGENT_INVESTIGATIONS {
        int id PK
        int exception_id FK
        json evidence_json
        string reasoning_summary
    }
    RECOMMENDATIONS {
        int id PK
        int investigation_id FK
        string category
        string explanation
        float confidence_score
    }
    APPROVALS {
        int id PK
        int recommendation_id FK
        int user_id FK
        string decision
        string note
    }
    AUDIT_LOGS {
        int id PK
        string entity_type
        int entity_id
        string action
        string actor
        datetime timestamp
    }
```

---

## 20. API Design

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/reconciliation/upload` | Upload the 3 CSVs (payment, bank, ledger) | Required |
| POST | `/api/reconciliation/run` | Trigger deterministic matching on uploaded data | Required |
| GET | `/api/reconciliation/runs` | List past reconciliation runs | Required |
| GET | `/api/reconciliation/{run_id}` | Get summary + metrics for a specific run | Required |
| GET | `/api/exceptions` | List exceptions (filterable by status/category) | Required |
| GET | `/api/exceptions/{id}` | Get full detail: evidence, AI investigation, recommendation | Required |
| POST | `/api/exceptions/{id}/approve` | Approve the AI's (or a manual) recommendation | Required |
| POST | `/api/exceptions/{id}/reject` | Reject with a note | Required |
| POST | `/api/exceptions/{id}/resolve` | Manually resolve without an AI recommendation | Required |
| GET | `/api/dashboard/metrics` | Aggregate KPIs for the dashboard | Required |

### Example: `POST /api/exceptions/{id}/approve`

- **Method:** POST
- **Path:** `/api/exceptions/{id}/approve`
- **Purpose:** Human confirms the AI's recommended resolution for a given exception
- **Request:**
```json
{
  "note": "Confirmed — fee matches merchant fee schedule for this period."
}
```
- **Response (200):**
```json
{
  "exception_id": "exc_1042",
  "status": "RESOLVED",
  "approved_by": "user_7",
  "approved_at": "2026-08-23T10:15:00Z"
}
```
- **Authentication:** Bearer JWT required
- **Error cases:** `404` if exception not found, `409` if exception already resolved, `422` if no recommendation exists yet to approve

*(Remaining endpoints follow the same documentation pattern — to be completed in the OpenAPI spec generated automatically by FastAPI during implementation.)*

---

## 21. Frontend / Dashboard

### 21.1 Pages

- Login
- Dashboard (KPI overview)
- Upload Data
- Reconciliation Run (progress/results)
- Transaction Explorer (browse all records across sources)
- Exception Queue (filterable list)
- Exception Details (evidence + AI reasoning trace)
- AI Investigation view (tool calls made, evidence retrieved)
- Human Approval (approve/reject/investigate/unresolved actions)
- Audit Trail (full log, filterable by entity)

### 21.2 Dashboard KPIs

- Total records
- Matched
- Match rate (%)
- Exceptions
- Auto-resolved (matched without needing an exception path)
- Human review (pending)
- Unresolved
- Total discrepancy amount (sum of unresolved exception differences)

### 21.3 Wireframe (ASCII)

```
+-----------------------------------------------------------+
|  ReconAgent                                   [User ▾]    |
+-----------------------------------------------------------+
|  KPI: Total 100 | Matched 78 | Match Rate 78%             |
|  KPI: Exceptions 22 | Auto-Resolved 78 | Review 15 | Unres 7|
+-----------------------------------------------------------+
|  [ Upload ]  [ Run Reconciliation ]  [ Exception Queue ]   |
+-----------------------------------------------------------+
|  Exception Queue                                           |
|  --------------------------------------------------------  |
|  ID       Category            Confidence   Status          |
|  exc_1001 PROCESSING_FEE      96%          RECOMMENDED     |
|  exc_1002 UNKNOWN             22%          NEEDS_REVIEW    |
|  exc_1003 MISSING_BANK_RECORD 71%          RECOMMENDED     |
+-----------------------------------------------------------+
```

---

## 22. Match Rate and Hackathon Metrics

### 22.1 Formulas

```
Match Rate           = Matched Records / Total Records × 100
Exception Rate        = Exception Records / Total Records × 100
Auto Resolution Rate  = Auto-Matched Records / Total Records × 100
Resolution Rate       = (Auto-Matched + Human-Approved Resolved) / Total Records × 100
Unresolved Rate       = Unresolved Records / Total Records × 100
```

### 22.2 Terminology Distinctions

- **Match** — the deterministic engine found agreement across sources; never touches the AI
- **Auto-resolution** — synonymous with "matched" in this MVP; no separate AI-driven auto-resolution path exists (see Section 16 — human approval is always required for exception-path records)
- **AI recommendation** — the agent's proposed classification/resolution for an exception, not yet final
- **Human-approved resolution** — an AI recommendation (or manual override) confirmed by a human
- **Unresolved** — no resolution has been confirmed; remains open

### 22.3 Example Calculation (100 records)

| Metric | Count | Rate |
|---|---|---|
| Total records | 100 | — |
| Matched (auto) | 78 | 78% match rate |
| Exceptions created | 22 | 22% exception rate |
| Human-approved resolutions (from exceptions) | 15 | — |
| Unresolved | 7 | 7% unresolved rate |
| Overall resolution rate | 93 / 100 | 93% |

---

## 23. Sample End-to-End Examples

**Example 1 — Exact match**
- Payment: ₹5,000 → Bank: ₹5,000 → Ledger: ₹5,000
- Result: Matched, no exception

**Example 2 — Processing fee**
- Payment/Settlement: ₹10,000 gross, fee ₹300, net ₹9,700
- Bank: ₹9,700
- Ledger: ₹10,000 expected
- Exception: `PROCESSING_FEE`
- AI explanation: "The ₹300 discrepancy matches the recorded processing fee on the payment/settlement record."
- Recommendation: "Approve reconciliation — difference fully explained by fee."

**Example 3 — Duplicate**
- Payment records: same reference/amount appears twice
- Bank: only one matching credit
- Exception: `DUPLICATE`
- AI explanation: "Reference ref_2291 appears twice in the payment/settlement source with identical amount and date; only one corresponding bank credit exists."
- Recommendation: "Flag the duplicate payment record for correction; treat the single bank credit as the true transaction."

**Example 4 — Missing record**
- Ledger + Bank show a ₹7,500 transaction
- No corresponding payment/settlement record found
- Exception: `MISSING_PAYMENT_RECORD`
- AI explanation: "No payment/settlement record with a matching reference or amount was found within the search window."
- Recommendation: "Investigate — possible manual bank deposit or unlogged payment channel."

**Example 5 — Unknown exception**
- Bank shows a ₹450 credit with no matching reference in ledger or payment source, and no plausible amount/date proximity match
- Exception: `UNKNOWN`
- AI explanation: "No supporting evidence found in either the payment/settlement or ledger sources for this bank credit."
- Recommendation: "Mark unresolved — requires manual investigation outside available data sources."

---

## 24. Security

**MVP implements a documented subset; full list is the production target.**

| Area | MVP | Production Target |
|---|---|---|
| Authentication | JWT-based login | JWT + refresh tokens, SSO optional |
| Authorization | Basic role check (`finance_controller` vs `admin`) | Full RBAC with per-action permissions |
| Data encryption | HTTPS in transit; DB credentials via env vars | Encryption at rest, KMS-managed secrets |
| Secrets management | `.env` file, excluded from repo | Vault/Secrets Manager integration |
| API security | JWT-protected endpoints | Rate limiting, WAF, mTLS between services |
| Input validation | Pydantic schema validation on all endpoints | Same, plus stricter file/type validation |
| CSV validation | Column presence, type checks, row limits | Same, plus malware/content scanning |
| SQL injection protection | SQLAlchemy ORM (parameterized queries) | Same, plus periodic scanning |
| Prompt injection protection | Agent tools are read-only and schema-constrained; agent output is structured, not executed as code | Same, plus input sanitization on any user-supplied text fed to the agent (e.g., bank `description`) |
| LLM data minimization | Only relevant per-exception evidence sent to the LLM, not full dataset | Same, plus PII redaction pipeline |
| Audit logging | All key actions logged to `audit_logs` | Same, plus tamper-evident/append-only storage |
| Sensitive financial data protection | Demo uses synthetic data only | Real data requires full compliance review (PCI-DSS scope assessment, etc.) |
| Rate limiting | Not implemented in MVP — documented as a gap | API gateway-level rate limiting |

---

## 25. AI Safety / Financial Safety

This section is mandatory and non-negotiable for the design:

- **The LLM must not directly modify financial records.** All writes to `payments`, `bank_transactions`, `ledger_entries` happen only via ingestion; the agent never writes to these tables.
- **Deterministic code owns all calculations.** Matching, differences, and tolerances are computed in application code, never inferred by the LLM.
- **Human approval is required for uncertain financial actions.** No exception-path record is marked `RESOLVED` without an explicit human `approve` action.
- **AI output must be grounded in retrieved records.** Every explanation must reference specific evidence returned by a tool call, not the model's general knowledge.
- **No unsupported claims.** If evidence doesn't support a category, the agent must return `UNKNOWN` rather than the closest-sounding guess.
- **Structured output.** The agent's classification, confidence, and recommendation are returned as structured (schema-validated) output, not free text, so downstream code can safely process them.
- **Tool access restrictions.** The agent has only the read-only tools listed in Section 14.3 — no tool exists for it to alter a financial record or trigger a real payment/transfer action.
- **Audit every AI decision.** Every investigation, recommendation, and confidence score is persisted, whether or not a human ultimately agrees with it.
- **Handle low-confidence cases safely.** Below a defined confidence threshold **[Proposed default: 60%, to be tuned]**, the case is automatically routed to `NEEDS_REVIEW` rather than presented as a quick-approve recommendation.
- **Unknown should remain UNKNOWN.** The system must expose genuine uncertainty rather than hide it behind a falsely confident classification.

---

## 26. Observability

| Category | What's Captured |
|---|---|
| Application logs | Standard request/response logs, errors, latencies |
| Agent execution logs | Each tool call made, its input/output, and the reasoning step it fed into |
| Reconciliation run logs | Start/end time, record counts, match/exception counts per run |
| Error tracking | Exceptions in code (distinct from financial "exceptions") captured with stack traces |
| Metrics | Match rate over time, exception rate, average time-to-resolution |
| AI latency | Time per agent investigation, per tool call |
| Token usage | Tokens consumed per investigation (for cost tracking, Section 36) |
| Failed tool calls | Any tool call that errors or returns no data |
| Human review rate | Proportion of exceptions requiring `NEEDS_REVIEW` vs quick-approve `RECOMMENDED` |

**Enterprise monitoring considerations (production target):** centralized log aggregation, distributed tracing across API → agent → LLM provider, alerting on anomalous exception spikes or agent error rates, dashboards for finance-ops SLA tracking.

---

## 27. Error Handling

| Failure Mode | Handling Strategy |
|---|---|
| Invalid CSV | Reject upload with a clear column/row-level error message; nothing partially ingested |
| Missing columns | Validate schema before ingestion; fail fast with the specific missing column named |
| Duplicate IDs (within a single source, unintended) | Flag at ingestion as a data-quality warning, distinct from the intentional `DUPLICATE` exception scenario |
| Invalid amounts (non-numeric, negative where not expected) | Row-level validation error, upload rejected or row skipped with a report |
| Missing records (cross-source) | Not a failure — this is the `MISSING_*` exception category by design |
| Database failure | Return `503`, retry with backoff on transient errors, do not silently drop writes |
| LLM failure (timeout, API error) | Retry with backoff; on repeated failure, mark the exception `NEEDS_REVIEW` with a system note ("AI investigation unavailable") rather than blocking the pipeline |
| Timeout | Per-call timeout on LLM/tool calls; exceeded timeout treated as an LLM failure per above |
| API failure (upstream LLM provider outage) | Deterministic matching still functions independently; only the AI-investigation stage degrades, and the system communicates this state clearly on the dashboard |
| Partial reconciliation | Runs are tracked with status (`in_progress`, `completed`, `failed`); a failed run doesn't corrupt prior runs' data |
| Unknown exception | Explicitly supported outcome, not an error — see Sections 18, 25 |

**The system should fail safely:** a failure in the AI layer must never be mistaken for, or silently converted into, a financial resolution.

---

## 28. Implementation Phases

| Phase | Goal | Key Tasks | Deliverables | Dependencies | Definition of Done |
|---|---|---|---|---|---|
| 1. Project Setup | Repo, tooling, environments ready | Init repo structure (Sec. 38), configure FastAPI + React skeletons, set up PostgreSQL locally | Running skeleton app | None | Both frontend and backend boot and talk to each other with a health-check endpoint |
| 2. Database | Schema in place | Implement all tables (Sec. 19), migrations via SQLAlchemy/Alembic | Migrated DB schema | Phase 1 | All tables created; ER diagram matches implementation |
| 3. Synthetic Data | Test dataset ready | Build generator script, produce 3 CSVs with labeled scenarios, 50+ records | `payment_settlement.csv`, `bank_statement.csv`, `internal_ledger.csv` + internal ground-truth mapping | Phase 2 (schema informs fields) | Dataset covers every MVP exception category at least once |
| 4. Data Ingestion | CSVs loadable into DB | Build `/upload` endpoint, CSV validation, adapter pattern (Sec. 37) | Working ingestion endpoint | Phases 2–3 | Uploading the 3 CSVs populates `payments`, `bank_transactions`, `ledger_entries` correctly |
| 5. Deterministic Reconciliation | Matching engine works | Implement matching logic (Sec. 13), compute match rate | `/run` endpoint + `reconciliation_results` populated | Phase 4 | Correctly matches exact-match records and flags all seeded exceptions |
| 6. Exception Management | Exceptions tracked | Build `exceptions` table population, exception queue API | `/exceptions` endpoints | Phase 5 | Every unmatched record produces exactly one tracked exception |
| 7. AI Investigation | Agent gathers evidence | Implement tools (Sec. 14.3), wire to DB | Tool layer functioning against seeded data | Phase 6 | Each tool returns correct evidence for a given reference_id |
| 8. Agent Workflow | Full agent reasoning loop | Implement LangGraph state machine (Sec. 14.6), structured output schema | End-to-end agent investigation per exception | Phase 7 | Agent produces classification + confidence + explanation for every exception type in the dataset |
| 9. Human Approval | Review loop works | Build approve/reject/resolve endpoints, `approvals` table | Working approval workflow | Phase 8 | A human can approve/reject an AI recommendation and see status update |
| 10. Dashboard | UI complete | Build all pages (Sec. 21) | Functional React dashboard | Phases 5–9 (data to display) | All KPIs and queue/detail views render real data end-to-end |
| 11. Razorpay Integration (exploratory) | Assess real integration feasibility | Research actual Razorpay APIs/sandbox **[To Be Verified]**, adapter stub | Findings documented; adapter interface defined, not necessarily implemented | Phase 4 (adapter pattern) | Team has a documented, honest answer on real-integration feasibility for the demo/production narrative |
| 12. Testing | Confidence in correctness | Execute test plan (Sec. 30) | Test suite passing | Phases 5–9 | Core scenarios (100% match, 0% match, mixed, duplicates, missing, fees, unknown, LLM-down) all pass |
| 13. Demo Preparation | Ready to present | Rehearse demo script (Sec. 32), prepare fallback for LLM outage | Demo run-through completed | All prior phases | Full demo flow runs without manual intervention in under 5 minutes |

---

## 29. Team Task Division (4-Person Team)

| Member | Primary Ownership | Key Deliverables |
|---|---|---|
| Member 1 | Backend / APIs / Database | FastAPI app structure, auth, all REST endpoints (Sec. 20), DB schema + migrations (Sec. 19) |
| Member 2 | Reconciliation Engine / Data Processing | Synthetic data generator (Sec. 12), Pandas-based matching engine (Sec. 13), ingestion + adapters (Sec. 37) |
| Member 3 | AI Agent / LangGraph / LLM | Tool implementations (Sec. 14.3), LangGraph state machine, structured output schemas, confidence logic (Sec. 17) |
| Member 4 | Frontend / Dashboard / UX | All React pages (Sec. 21), KPI visualizations (Recharts), exception queue/detail UI, approval UI |

**Shared integration responsibilities:**
- Agreeing on API contracts (request/response shapes) before Phase 4
- Joint review of the exception taxonomy (Sec. 18) so backend, agent, and frontend all use identical enum values
- End-to-end integration testing (Phase 12) owned jointly
- Demo script rehearsal (Phase 13) — all members present, one drives, all can answer questions

---

## 30. Testing Strategy

| Test Type | Coverage |
|---|---|
| Unit tests | Matching engine functions, tolerance logic, difference calculation, individual agent tools |
| Integration tests | Ingestion → matching → exception creation pipeline; agent tool calls against a real (test) DB |
| API tests | All endpoints (Sec. 20) — happy path + error cases |
| Data validation tests | CSV schema validation, malformed row handling |
| Reconciliation correctness tests | Verify seeded scenarios (Sec. 12.2) produce the expected match/exception outcome |
| Agent evaluation | See Section 31 |
| End-to-end tests | Full flow from upload through human approval, via API or UI automation |
| Security tests | Auth-required endpoints reject unauthenticated requests; input validation rejects malformed payloads |

### Especially Test

- 100% matching dataset (all records align cleanly)
- 0% matching dataset (every record is an exception)
- Mixed dataset (realistic blend — the actual 50+ record hackathon dataset)
- Duplicate records
- Missing records (each of the three missing-record scenarios)
- Fee discrepancies
- Unknown/unresolvable cases
- LLM unavailable (agent layer down, matching engine must still function)

---

## 31. AI Evaluation

**Metrics:**

- Root cause classification accuracy (against the synthetic dataset's internal ground-truth labels, Sec. 12.1)
- Explanation correctness (manual review: does the explanation cite evidence that actually supports the claim?)
- Evidence grounding (does every claim in the explanation trace to a tool result?)
- False positive rate (agent confidently classifies something incorrectly)
- False negative rate (agent returns `UNKNOWN` when a confident, correct classification was actually derivable)
- Recommendation accuracy (does the recommended action match what a human reviewer judges to be correct?)
- Human override rate (how often humans reject the AI's recommendation)
- Unresolved rate (how often the system honestly reports `UNKNOWN`/unresolved rather than forcing a guess)

**We do not claim any specific performance numbers in this document** — actual accuracy/override rates must be measured against the team's own synthetic dataset during Phase 12 testing before being cited in the demo or presentation.

---

## 32. Demo Scenario

**Target: 3–5 minutes.**

1. Login as a finance controller
2. Upload the 50+ record synthetic dataset (3 CSVs)
3. Run reconciliation
4. Show the resulting match rate and KPI dashboard
5. Open the Exception Queue — show the mix of categories
6. Open a "difficult" transaction (e.g., a fee-difference or missing-record case)
7. Show the AI Investigation view: which tools were called, what evidence was retrieved
8. Show the generated explanation and confidence score, tied explicitly to the evidence
9. Approve the recommendation
10. Return to the dashboard — show updated KPIs (resolution count increased)
11. Open the Audit Trail — show the full chain from upload → match → exception → investigation → approval

**What judges should see:** a clear separation between deterministic math (fast, exact, boring — in a good way) and AI reasoning (evidence-grounded, explained, never unilaterally final), plus a complete, inspectable audit trail.

---

## 33. Before vs After

**Traditional process:**
```
Manual → Compare → Investigate → Search records → Decide → Document
```

**ReconAgent:**
```
Automated Match → Detect Exception → AI Investigate → AI Explain → AI Recommend → Human Approve → Audit
```

**Expected benefits (qualitative, no invented figures):**
- Faster identification of which records actually need human attention (deterministic matching removes the "easy" majority immediately)
- Consistent, evidence-grounded explanations instead of ad hoc, undocumented reasoning
- A structured, queryable audit trail instead of scattered notes/emails
- A human is still fully in control of every financial resolution — nothing is claimed to be "hands-off"

We deliberately avoid citing specific percentage time-savings or cost-savings figures, since no real-world deployment data exists yet to support such claims.

---

## 34. Production Architecture — **FUTURE / PRODUCTION**

> Everything in this section is explicitly out of scope for the hackathon MVP.

Post-MVP, the system could evolve to include:

- API gateway for centralized auth, rate limiting, and routing
- Dedicated identity/auth service (SSO, enterprise directory integration)
- PostgreSQL, likely with read replicas at scale
- Object storage for uploaded files/artifacts
- Message queue (e.g., for async reconciliation runs at high volume)
- Worker services (Celery or similar) for background matching/agent runs
- LLM gateway (centralized routing, model fallback, cost tracking)
- Secrets management (Vault/KMS)
- Full monitoring/observability stack
- Dedicated audit service (potentially with tamper-evident/append-only storage)
- Real Razorpay API integration **[To Be Verified feasibility]**
- Bank integrations (direct feeds instead of manual statement upload)
- Accounting/ERP integrations (e.g., syncing resolved exceptions back into the ledger system)

```mermaid
flowchart TB
    subgraph Edge
        GW[API Gateway]
    end
    subgraph Services
        AUTHS[Identity/Auth Service]
        RECSVC[Reconciliation Service]
        AGENTSVC[Agent Orchestration Service]
        AUDITSVC[Audit Service]
    end
    subgraph Data Layer
        PGPROD[(PostgreSQL - Primary + Replicas)]
        OBJ[(Object Storage)]
        Q[(Message Queue)]
    end
    subgraph External
        RZP[Razorpay API - future]
        BANK[Bank API - future]
        ERP[ERP/Accounting API - future]
        LLMGW[LLM Gateway]
    end

    GW --> AUTHS
    GW --> RECSVC
    GW --> AGENTSVC
    RECSVC --> Q
    Q --> AGENTSVC
    AGENTSVC --> LLMGW
    RECSVC --> PGPROD
    AGENTSVC --> PGPROD
    AUDITSVC --> PGPROD
    RECSVC --> OBJ
    RECSVC -.future.-> RZP
    RECSVC -.future.-> BANK
    RECSVC -.future.-> ERP
```

---

## 35. Scalability

| Scale | Considerations |
|---|---|
| 100 records (hackathon) | Single-process FastAPI + Pandas is more than sufficient; synchronous processing is fine |
| 10,000 records | Introduce async/background job processing for the matching run; paginate all list endpoints; ensure DB indexes on join keys (`reference_id`) are in place |
| 1,000,000+ records | Batch processing for ingestion and matching; queue-based worker architecture for agent investigations (only exceptions go through AI, which is itself a natural scale-limiter); caching for repeated/similar investigations; horizontal scaling of the agent-worker pool; database partitioning/archiving strategy for historical runs |

**Key scaling principle carried through from the MVP design:** because AI is only invoked on the exception subset (not every record), the AI-cost and AI-latency burden grows with the *exception rate*, not the *total volume* — which is the primary lever for cost control at scale (see Section 36).

---

## 36. Cost Optimization

- **Deterministic matching first** — the majority of records (ideally) never reach the LLM at all
- **Send only exceptions to the LLM** — never the full dataset
- **Structured prompts** — minimize token overhead with tightly-scoped, schema-driven prompts rather than open-ended narrative prompts
- **Smaller model for classification** — a lighter model may be sufficient for straightforward categorization; reserve larger/more capable models for ambiguous cases **[Proposed, model selection to be tuned based on actual accuracy needs]**
- **Larger model only for complex cases** — e.g., escalate to a stronger model only when initial confidence is low
- **Cache repeated investigations** — if the same reference/pattern recurs, avoid recomputation
- **Limit context** — only pass the specific evidence records relevant to the exception under investigation, not adjacent unrelated records
- **Avoid sending full datasets to the LLM** — enforced architecturally by the tool-based evidence-retrieval design (Section 14), not just as a prompting guideline

---

## 37. API / Data Integration Strategy

### 37.1 Adapter Pattern

```
PaymentDataAdapter
BankDataAdapter
LedgerDataAdapter
```

Each adapter exposes a common internal interface (e.g., `load() -> List[NormalizedRecord]`) regardless of the underlying source format. This means the matching engine and downstream pipeline never need to know whether data came from a CSV upload or a live API.

- **MVP:** CSV adapters (`PaymentCSVAdapter`, `BankCSVAdapter`, `LedgerCSVAdapter`)
- **Future:** `RazorpayAPIAdapter` **[To Be Verified feasibility]**, `BankAPIAdapter`, `ERPAPIAdapter`

**Why adapters make the system extensible:** swapping a CSV upload for a live API integration later requires only implementing a new adapter that conforms to the same interface — no changes to the matching engine, agent, database schema, or frontend are required.

---

## 38. Project Directory Structure

```
reconagent/
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Login.tsx
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Upload.tsx
│   │   │   ├── ReconciliationRun.tsx
│   │   │   ├── TransactionExplorer.tsx
│   │   │   ├── ExceptionQueue.tsx
│   │   │   ├── ExceptionDetail.tsx
│   │   │   ├── AuditTrail.tsx
│   │   ├── components/
│   │   │   ├── KpiCard.tsx
│   │   │   ├── ExceptionTable.tsx
│   │   │   ├── EvidencePanel.tsx
│   │   │   ├── ApprovalActions.tsx
│   │   ├── api/
│   │   │   └── client.ts
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   └── vite.config.ts
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── reconciliation.py
│   │   │   ├── exceptions.py
│   │   │   ├── auth.py
│   │   │   └── dashboard.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   └── security.py
│   │   ├── db/
│   │   │   ├── models.py
│   │   │   ├── session.py
│   │   │   └── migrations/
│   │   ├── matching/
│   │   │   ├── engine.py
│   │   │   └── rules.py
│   │   ├── agent/
│   │   │   ├── graph.py
│   │   │   ├── tools.py
│   │   │   └── schemas.py
│   │   ├── adapters/
│   │   │   ├── payment_csv_adapter.py
│   │   │   ├── bank_csv_adapter.py
│   │   │   └── ledger_csv_adapter.py
│   │   └── audit/
│   │       └── logger.py
│   ├── tests/
│   ├── requirements.txt
│   └── alembic.ini
├── data/
│   ├── generator/
│   │   └── generate_synthetic_data.py
│   ├── payment_settlement.csv
│   ├── bank_statement.csv
│   └── internal_ledger.csv
├── docs/
│   └── ReconAgent_Master_Spec.md
├── tests/
│   └── e2e/
├── scripts/
│   └── setup_local_env.sh
├── infra/
│   └── docker-compose.yml   (optional)
└── README.md
```

---

## 39. Environment Variables

```
DATABASE_URL=postgresql://user:password@localhost:5432/reconagent
JWT_SECRET=<generated-secret>
LLM_API_KEY=<provider-api-key>
RAZORPAY_KEY_ID=<optional-until-real-integration>
RAZORPAY_KEY_SECRET=<optional-until-real-integration>
```

Razorpay credentials are marked optional and are only required if/when a real integration (Phase 11, exploratory) is pursued. **Never commit real secrets to the repository** — use `.env` files excluded via `.gitignore`, and a `.env.example` template for onboarding.

---

## 40. API Response Examples

**Reconciliation run:**
```json
{
  "run_id": "run_2044",
  "run_at": "2026-08-23T09:00:00Z",
  "total_records": 100,
  "matched_count": 78,
  "exception_count": 22,
  "match_rate": 78.0
}
```

**Matched transaction:**
```json
{
  "reference_id": "ref_1029",
  "match_status": "MATCHED",
  "match_confidence": 1.0,
  "sources_matched": ["payment", "bank", "ledger"]
}
```

**Exception:**
```json
{
  "exception_id": "exc_1042",
  "reference_id": "ref_1042",
  "difference_amount": 300.00,
  "status": "RECOMMENDED",
  "created_at": "2026-08-23T09:00:05Z"
}
```

**AI investigation:**
```json
{
  "investigation_id": "inv_501",
  "exception_id": "exc_1042",
  "tools_called": ["get_settlement", "get_bank_record", "get_ledger_record", "check_fee"],
  "evidence": {
    "payment_fee": 300.00,
    "ledger_expected": 10000.00,
    "bank_received": 9700.00
  }
}
```

**Recommendation:**
```json
{
  "recommendation_id": "rec_701",
  "investigation_id": "inv_501",
  "category": "PROCESSING_FEE",
  "explanation": "The 300 discrepancy matches the recorded processing fee on the payment/settlement record.",
  "confidence_score": 0.96
}
```

**Approval:**
```json
{
  "approval_id": "app_301",
  "recommendation_id": "rec_701",
  "user_id": "user_7",
  "decision": "APPROVED",
  "note": "Confirmed against fee schedule.",
  "decided_at": "2026-08-23T10:15:00Z"
}
```

---

## 41. Audit Trail

Every significant action is logged as an immutable entry:

```
User uploaded dataset
  → reconciliation run started
  → record matched (per record)
  → exception created (per unmatched record)
  → AI investigation performed (tools called + evidence)
  → recommendation generated (category + confidence + explanation)
  → human decision recorded (approve/reject/investigate/unresolved)
  → record resolved (if approved)
```

Each log entry records: `entity_type`, `entity_id`, `action`, `actor` (user or `system`/`agent`), a `payload_json` snapshot of relevant state, and a `timestamp`. This makes every important action traceable end-to-end — from raw upload to final human decision.

---

## 42. Known Limitations

- The MVP relies on **synthetic data**, not verified real Razorpay/bank/ERP data
- Only **three** initial data sources are supported (payment/settlement, bank, ledger) — no multi-currency, multi-entity, or multi-bank-account complexity yet
- **No production bank integration** — data enters via CSV upload only
- **LLM uncertainty** is inherent — confidence scores are a decision-support signal, not a guarantee
- **Limited exception categories** — the MVP taxonomy (Section 18) does not cover every possible real-world reconciliation scenario
- **No automatic financial transfer or ledger write-back** — the system recommends and records decisions but does not execute any real financial movement
- **Limited historical intelligence** — pattern-matching against prior resolutions is not implemented in the MVP (documented as a future enhancement)
- **Hackathon-scale infrastructure** — the architecture is intentionally simple (single service, synchronous processing) and is not production-hardened

---

## 43. Future Enhancements — **explicitly NOT part of the MVP**

- Real Razorpay integration **[To Be Verified feasibility]**
- Real bank integrations (direct feeds)
- ERP/accounting system integration (write-back of resolved exceptions)
- Continuous/scheduled reconciliation (not just on-demand runs)
- Predictive exception detection (flagging likely future mismatches before they occur)
- Historical anomaly detection (recurring pattern recognition across runs)
- Automated recurring reconciliation schedules
- Slack/email alerts for new exceptions or approvals needed
- Multi-tenant architecture (supporting multiple businesses/entities)
- Advanced policy engine (configurable auto-approval thresholds per organization)
- FinOps/finance analytics beyond reconciliation
- Natural-language finance assistant ("ask ReconAgent" conversational interface over the audit trail and metrics)

---

## 44. Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| Incorrect AI explanation | Medium–High (misleads human reviewer) | Medium | Mandatory evidence-grounding, human approval gate, confidence thresholds routing low-confidence cases to review |
| False match (deterministic engine wrongly matches two unrelated records) | High (hides a real discrepancy) | Low | Conservative tolerance/date-window defaults, thorough test coverage (Section 30) |
| False exception (flags a genuinely fine record) | Medium (adds unnecessary review burden) | Medium | Tunable tolerance thresholds, test against seeded exact-match scenarios |
| LLM outage | Medium (blocks AI investigation stage only) | Low–Medium | Deterministic matching unaffected; exceptions fall back to `NEEDS_REVIEW` with a system note (Section 27) |
| Data corruption on ingestion | High | Low | Schema validation at upload, transactional writes |
| Duplicate data (unintentional, from re-upload) | Medium | Medium | Idempotency checks on ingestion, run-scoped record isolation |
| Security breach | High | Low (hackathon context; higher in production) | Documented security subset (Section 24) for MVP, full list for production |
| Prompt injection (e.g., via a crafted bank `description` field) | Medium | Low–Medium | Agent tools are schema-constrained; agent cannot execute arbitrary instructions found in data fields; output is structured and validated |
| High LLM cost | Low–Medium (demo-scale) | Low (demo-scale) / Medium (at production scale) | Cost optimization strategy (Section 36) |
| API failure (internal or LLM provider) | Medium | Low–Medium | Retry/backoff, graceful degradation (Section 27) |

---

## 45. Requirements Traceability

| Requirement | Feature | Component | Test |
|---|---|---|---|
| Process 50+ record synthetic dataset | Synthetic data generator | `data/generator/generate_synthetic_data.py` | Dataset row-count + coverage test |
| Report match rate | Deterministic matching engine | `matching/engine.py` | Reconciliation correctness tests |
| Report auto-resolved records | Matching engine output | `reconciliation_results` table | Reconciliation correctness tests |
| Report exceptions | Exception creation logic | `exceptions` table + `/api/exceptions` | Exception scenario tests |
| Report unresolved records | Human review status tracking | `approvals` table, `UNRESOLVED` status | E2E test: unresolved path |
| Report reasons for exceptions | AI classification + explanation | `agent/graph.py`, `recommendations` table | Agent evaluation (Section 31) |
| Report human review cases | Approval workflow | `/api/exceptions/{id}/approve|reject` | Human-in-the-loop E2E test |
| Deterministic financial calculations | Matching engine, no LLM math | `matching/engine.py` | Unit tests on `calculate_difference` |
| Human approval gate | Approval workflow enforcement | `approvals` table, status machine | Test: no `RESOLVED` status without an `approvals` row |
| Full audit trail | Audit logging | `audit/logger.py`, `audit_logs` table | Audit completeness test (every action type produces a log entry) |

---

## 46. Definition of Done (MVP)

- [ ] 50+ records processed across the three synthetic datasets
- [ ] All three datasets (payment/settlement, bank, ledger) accepted via upload
- [ ] Deterministic matching engine correctly matches exact-match records
- [ ] Match rate, exception rate, and related metrics calculated correctly
- [ ] Every seeded exception scenario (Section 12.2) is correctly detected
- [ ] AI agent investigates every exception using its defined tool set
- [ ] AI explanation is grounded in retrieved evidence (manually spot-checked)
- [ ] Human approval workflow (approve/reject/investigate/unresolved) functions end-to-end
- [ ] Audit trail captures every step from upload through resolution
- [ ] Dashboard displays all defined KPIs accurately
- [ ] Full demo flow (Section 32) runs end-to-end without manual data patching

---

## 47. Final Project Summary

**Problem:** Finance teams manually reconcile payment/settlement, bank, and internal ledger records to catch mismatches — a slow, error-prone, and poorly-audited process.

**Existing Gap:** No widely-available tool combines multi-source (3-way) deterministic matching with AI-driven, evidence-grounded exception investigation, transparent confidence scoring, mandatory human approval, and full auditability, in one system.

**Solution:** ReconAgent — deterministic matching for speed and correctness, an AI agent for investigation and explanation of exceptions only, and a human-in-the-loop approval gate for every resolution.

**Why AI:** Root-cause investigation across ambiguous, natural-language-shaped evidence (fee schedules, refund notes, timing patterns) is exactly the kind of reasoning task that benefits from an LLM — but only when constrained to evidence actually retrieved via tools, not free-form guessing.

**Why Agentic:** The system doesn't classify in one shot from a static prompt — it follows an explicit multi-step process (collect evidence → analyze → classify → score confidence → recommend), using tools to gather facts before reasoning, mirroring how a human analyst would actually investigate.

**Data:** Synthetic, 50+ records, spanning every required exception scenario, explicitly labeled as synthetic pending any authorized real/sandbox data.

**Architecture:** React/Vite frontend, FastAPI/Python backend, PostgreSQL, Pandas-based deterministic matching engine, LangGraph-orchestrated AI agent with a constrained, read-only tool set.

**Tech Stack:** See Section 10.

**MVP:** Three-source ingestion, deterministic matching, exception detection, AI investigation and recommendation, human approval, full audit trail, and a KPI dashboard — all demonstrable end-to-end on a 50+ record dataset.

**Measurable Outcome:** Match rate, auto-resolved count, exception count (by category), human-review count, unresolved count — all computed and displayed for the demo dataset.

**Future Vision:** Real Razorpay/bank/ERP integration, continuous reconciliation, predictive/historical anomaly detection, multi-tenant production deployment — all explicitly deferred beyond the hackathon MVP.

---

## 48. Important Architectural Principles

These are non-negotiable and govern every implementation decision in this project:

1. Financial calculations must be deterministic.
2. AI must reason over evidence — never over assumption or general knowledge alone.
3. AI must not fabricate financial facts.
4. Human approval is required for uncertain financial actions.
5. Every decision must be auditable.
6. Synthetic data is used for the MVP unless authorized real/sandbox data is available and verified.
7. AI is used selectively — only on exceptions, never on every transaction.
8. The system must expose unresolved exceptions instead of hiding uncertainty behind a false classification.
9. Security and privacy are first-class requirements, even at hackathon scale.
10. MVP architecture must remain simple enough to implement during the hackathon — no technology is added without a stated reason (Section 10.1).

---

*End of Master Specification. This document should be treated as a living blueprint — update it as the team makes concrete implementation decisions, and revise all Razorpay-specific sections once verified source material is available.*

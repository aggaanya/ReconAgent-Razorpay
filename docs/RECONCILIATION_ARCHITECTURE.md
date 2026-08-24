# Reconciliation Architecture — Track 04 Finance-Ops Loop

Status: **implemented** (`backend/app/services/reconciliation.py`,
`backend/app/services/reconciliation_synthetic.py`, tool
`reconcile_transactions` in `app.ai.tools.reconciliation`, HTTP endpoint
`POST /api/v1/ai/reconcile`, evaluation harness
`scripts/reconcile_benchmark.py`).

This document specifies the deterministic batch reconciliation loop: the
problem it solves, its inputs, its fixed matching rules, the exception
taxonomy, how accuracy is measured against ground truth without ever
letting ground truth influence decisions, and how the loop plugs into the
existing LangGraph finance stack.

---

## Contents

1. [Problem statement](#1-problem-statement)
2. [Why this direction](#2-why-this-direction)
3. [Inputs](#3-inputs)
4. [Matching rules](#4-matching-rules)
5. [Exception taxonomy](#5-exception-taxonomy)
6. [Ground-truth methodology](#6-ground-truth-methodology)
7. [Match rate](#7-match-rate)
8. [Accuracy](#8-accuracy)
9. [Throughput](#9-throughput)
10. [LangGraph flow](#10-langgraph-flow)
11. [Engine responsibility vs LLM responsibility](#11-engine-responsibility-vs-llm-responsibility)
12. [Example evaluation (100 records)](#12-example-evaluation-100-records)
13. [Limitations](#13-limitations)
14. [Known unresolved cases](#14-known-unresolved-cases)

---

## 1. Problem statement

A payment platform constantly drifts out of sync with itself: money is
captured but never settles, settles twice, settles in the wrong currency,
settles against a failed payment, or arrives with no attributable
reference at all. Operations teams need a periodic, explainable answer to
one question:

> For every transaction we expected to settle, did money actually move,
> exactly once, for exactly the right amount — and if not, why not?

Track 04 answers that question over a bounded dataset and produces:

- **volume metrics** (records processed, matched, exceptions),
- a **match rate**,
- an **honest exception list** where every exception carries a stated,
  typed reason,
- **throughput / processing time**, and
- a **measured accuracy against internal ground truth** inside the
  evaluation harness only.

Per `docs/ReconAgent_Master_Spec.md` §22/§31/§46, the loop must compute
match rate and exception rate correctly, must never call an LLM during
matching, and must report a classification reason for every exception.

## 2. Why this direction

The master spec sketches reconciliation across three sources (payment /
bank / ledger). This implementation deliberately starts with the two
sources the system already owns end-to-end — **payments vs settlement
expectations** — because:

- The existing Finance Intelligence Engine already models both domains
  (`Payment`, `Settlement`) with pinned vocabularies; reusing them keeps
  one source of truth for status semantics.
- Every exception type the two-source loop can produce (amount mismatch,
  duplicate, missing record, wrong currency, invalid state, unattributable
  reference) is structurally identical to its three-source counterpart;
  adding a third source later extends the same rules rather than
  replacing them.
- A synthetic generator lets us prove correctness *exactly* (100%
  measured accuracy on engineered corruption cases) instead of
  approximately on unknown production data.

Vocabulary bridge: what the master spec calls a "ledger/bank record"
this implementation calls a **settlement expectation** — one row per
expected money movement, carrying an attribution reference back to its
payment.

## 3. Inputs

The engine consumes pure domain records (Pydantic frozen models in
`app/schemas/reconciliation.py`) — **not** ORM rows. Rationale: real
`Settlement` rows are batch-level aggregates with no per-payment
reference, while reconciliation needs line-level attribution. The
records mirror a recon-breakup shape:

| Record | Key fields |
|---|---|
| `ReconciliationPayment` | `payment_id`, `amount_minor` (int, minor units), `currency` (ISO), `status`, optional `fee_minor` / `tax_minor` / `created_on` |
| `ReconciliationSettlementLine` | `settlement_id`, `payment_id` (attribution reference; may be blank/garbage), `amount_minor`, `currency`, optional `settled_on` |
| `ReconciliationRefund` | `refund_id`, optional `payment_id`, `amount_minor`, `currency`, `status` |

Contract details:

- amounts are **exact integer minor units** — no floats anywhere;
- duplicate ids on any side raise `ValueError` immediately (a batch
  that cannot be keyed cannot be reconciled);
- inputs are immutable; the engine never mutates or annotates them;
- the engine signature is exactly
  `reconcile(payments, settlements, refunds=None, *, max_settlement_delay_days=None)`
  — records and configuration only, **no channel** through which
  outcomes could enter (pinned by test). Omitting the optional
  arguments reproduces the legacy two-record behavior exactly;
- fee/tax are consumed from the *payment* record verbatim — never
  re-derived from a rate table (none exists in this project).

## 4. Matching rules

Rules are evaluated in fixed precedence; the first matching rule decides.
Determinism is absolute: same inputs ⇒ same results, same order (payments
keep input order, then unattributable settlements, then orphaned
processed refunds).

The engine reconciles against an **expected settlement**, computed from
whatever components are recorded on its inputs:

```
expected = gross_amount − Σ(processed refund amounts) − fee − tax
```

Only recorded values participate; absent components contribute zero and
never get invented.

| # | Rule | Fires when | Result |
|---|---|---|---|
| R0 | scope filter | payment has no settlement lines at all AND its status makes settling impossible (`created`, `authorized`, `failed`) | excluded from the run entirely |
| R1 | invalid state | payment status outside the known vocabulary, OR known-but-ineligible (e.g. `failed`) yet settled anyway | `INVALID_STATUS` |
| R2 | missing side | captured/refunded payment with zero settlement lines | `MISSING_SETTLEMENT` (reason names any processed refunds) |
| R3 | duplicate | >1 settlement line attributed to one payment | `DUPLICATE_SETTLEMENT` (beats all amount checks — the amount question is moot when money moved twice) |
| R4 | currency guard | settlement currency ≠ payment currency | `CURRENCY_MISMATCH` (no cross-currency comparison is ever attempted) |
| R8 | refund integrity | processed refunds exceed the payment amount, or a processed refund is in another currency; a processed refund referencing an unknown payment becomes an orphan case | `REFUND_MISMATCH` / `MISSING_PAYMENT` anchored on the refund id (fires *before* any settlement math — integrity precedes arithmetic) |
| R9 | timing window | `max_settlement_delay_days` configured AND both dates present AND `settled_on − created_on > window`; disabled by default (`None`) for backward compatibility | `SETTLEMENT_DELAY` (money can be exact; lateness alone flags it) |
| R5 | amount gate | single line, same currency: settled amount equals the expected settlement → match; otherwise mismatch with `difference = actual − expected`. With no components available the mismatch is legacy `AMOUNT_MISMATCH`; once fee/tax/refunds were accounted, a residual difference is `UNEXPLAINED_SETTLEMENT_DIFFERENCE` | `MATCHED` / `AMOUNT_MISMATCH` / `UNEXPLAINED_SETTLEMENT_DIFFERENCE` |
| R6 | phantom reference | attribution reference matches no payment in the batch | `MISSING_PAYMENT`, anchored on the phantom id |
| R7 | unusable reference | attribution reference blank or whitespace-only (settlement lines *and* blank-reference processed refunds) | `UNRESOLVED`, anchored on the record's own id |

## 5. Exception taxonomy

Eleven terminal statuses, one enum (`ReconciliationStatus`), shared by the
engine, the schema layer, the generator, and the API:

| Status | Family | Business meaning |
|---|---|---|
| `MATCHED` | healthy | settled once, equal to gross or to the expected net of recorded components |
| `AMOUNT_MISMATCH` | exception | wrong amount with no components recorded (legacy shape) |
| `UNEXPLAINED_SETTLEMENT_DIFFERENCE` | exception | residual difference after fees/tax/refunds were already accounted |
| `MISSING_SETTLEMENT` | exception | money expected, never arrived |
| `DUPLICATE_SETTLEMENT` | exception | money moved more than once |
| `MISSING_PAYMENT` | exception | settlement or processed refund arrived for an unknown payment |
| `CURRENCY_MISMATCH` | exception | settled in the wrong currency |
| `INVALID_STATUS` | exception | state machine violated (settled a failed payment / unknown status) |
| `REFUND_MISMATCH` | exception | refund records fail integrity (over-refund / cross-currency) |
| `SETTLEMENT_DELAY` | exception | settlement arrived beyond the configured tolerance window |
| `UNRESOLVED` | human review | reference too broken to attribute automatically |

Every result carries `reason` (plain-English sentence),
`exception_type` (equals the status for exceptions), and the component
breakdown behind the expectation (`gross_amount_minor`, `fee_minor`,
`tax_minor`, `refunded_total_minor`); every exception is a typed fact the
LLM may later *describe* but never re-classify.

Mapping to master-spec §12.2 scenarios: exact match → `MATCHED`;
incorrect amount → `AMOUNT_MISMATCH` (legacy shape) or
`UNEXPLAINED_SETTLEMENT_DIFFERENCE` (components present); fee-driven
differences → expected in the net calculation, surfacing as exceptions
only when they do not explain the observed amount; duplicate →
`DUPLICATE_SETTLEMENT`; missing bank/ledger record → `MISSING_SETTLEMENT`;
orphan bank credit → `MISSING_PAYMENT`; refund anomalies →
`REFUND_MISMATCH`; late settlement → `SETTLEMENT_DELAY`.

## 6. Ground-truth methodology

Ground truth exists **only** inside the evaluation harness
(`reconciliation_synthetic.generate_synthetic_batch` →
`GroundTruthEntry` list; consumed by `evaluate_ground_truth` in tests and
`scripts/reconcile_benchmark.py`). The isolation is structural:

1. The generator engineers each case by construction: it knows what it
   corrupted and emits the expected status alongside the records — plus,
   where meaningful, the expected settlement arithmetic (expected/actual
   minor units), the exception type, and a reason fragment that must
   appear in the engine's reason string.
2. The engine receives only `(payments, settlements, refunds)` and a
   delay-window config — its signature has no truth parameter (enforced
   by test).
3. Serving paths (`tool`, graph node, `POST /api/v1/ai/reconcile`) return
   `accuracy: null` always; the field exists so clients cannot confuse
   "not measured here" with "zero".
4. The harness compares engine decision vs expected status per
   `case_id == source_transaction_id`; any disagreement — status, pinned
   amounts, or missing reason fragment — is listed as a mismatch with a
   `kind` and fails the run.

The default batch (`seed=42`, `size=100`) embeds the designed
distribution below; a drift test pins the constant so silent taxonomy
changes are caught:

| Expected outcome | Cases in 100 |
|---|---|
| `MATCHED` | 58 |
| `UNEXPLAINED_SETTLEMENT_DIFFERENCE` | 7 |
| `REFUND_MISMATCH` | 7 |
| `AMOUNT_MISMATCH` | 6 |
| `MISSING_SETTLEMENT` | 5 |
| `SETTLEMENT_DELAY` | 5 |
| `DUPLICATE_SETTLEMENT` | 3 |
| `MISSING_PAYMENT` | 3 |
| `CURRENCY_MISMATCH` | 2 |
| `INVALID_STATUS` | 2 |
| `UNRESOLVED` | 2 |

`MATCHED` sub-shapes alternate deterministically between gross matches,
net-of-fee/tax matches, and net-of-refund+fee/tax matches, so every
matching path is exercised on the canonical batch.

## 7. Match rate

```
match_rate = matched_count / total_records × 100      (2 decimal places)
```

`total_records` counts everything that entered the comparison (matched +
all exceptions, including exclusions' counterparts). A batch with zero
records yields `null`, never a fabricated 0% or 100%.

## 8. Accuracy

Measured **only** in the evaluation harness:

```
accuracy = correct_decisions / total_decisions × 100  (2 decimal places)
```

A decision is *correct* when the engine's terminal status equals the
generator's expected status for that case (pinned amounts and reason
fragments are verified additionally when present). On the canonical batch
the invariant is exact: **accuracy = 100.0, mismatches = []**. Any
regression fails tests and the benchmark exits nonzero. Accuracy is never
reported by serving endpoints (see §6.3).

Detection metrics treat "is an exception" as the positive class:

```
false_positives = truth-matched cases the engine flagged
false_negatives = truth-exception cases the engine matched
precision = TP / (TP + FP)      recall = TP / (TP + FN)
F1 = 2·precision·recall / (precision + recall)   (all percentages,
zero denominators yield null — never a fabricated 0 or 100)
```

On the canonical batch: FP = 0, FN = 0, precision = recall = F1 = 100%.

## 9. Throughput

```
throughput = total_records / elapsed_seconds          (records/sec)
processing_time_ms = elapsed × 1000                   (3 decimals)
```

Timing measures only `build_report` (engine execution), not generation or
HTTP transport. Timing fields are the only sanctioned run-to-run variance;
tests exclude them when asserting determinism.

## 10. LangGraph flow

The loop enters the existing plan → execute → analyze → interpret graph
unchanged — no new nodes:

1. **Plan.** The LLM planner sees `reconcile_transactions` in its catalog
   (derived from the registry at call time). If planning fails or the
   question contains *"reconcil"*, the deterministic fallback routes the
   run to `reconcile_transactions` **alone** — its report already covers
   matches and exceptions, so dashboard tools stay out of the run.
2. **Execute.** `execute_tools_node` halts on a missing database session
   only when a *selected* tool requires one; reconciliation declares
   `requires_session = False`, so a reconcile-only plan runs with no
   database at all.
3. **Analyze.** The Signal Analysis Engine finds nothing to flag in a
   recon envelope (its detectors target dashboard metrics) — no signals
   are fabricated.
4. **Interpret.** The LLM receives the full envelope (summary + typed
   exception list) as facts and writes narrative about them.

Outside chat, `POST /api/v1/ai/reconcile` invokes the tool directly
(same validation, same envelope) and attaches the narrative optionally —
`explain=false` works with zero LLM configuration.

## 11. Engine responsibility vs LLM responsibility

| Deterministic engine (source of truth) | LLM (words about facts) |
|---|---|
| decides every status via rules R0–R9 | describes returned numbers |
| computes expected settlements from recorded components | explains the arithmetic in plain language, never recomputes it |
| computes match rate, throughput, breakdown | highlights what needs human review first |
| guarantees identical output for identical inputs | may fail; facts remain intact (`status=partial`) |
| never sees ground truth | never sees ground truth either |

This preserves the project invariant from `docs/AI_ARCHITECTURE.md` §4:
*"AI-assisted investigation, not AI-authoritative computation."*

## 12. Example evaluation (100 records)

Actual output of `scripts/reconcile_benchmark.py` on the canonical batch:

```
================================================================
RECONCILIATION EVALUATION
================================================================
Dataset              : synthetic seed=42 size=100
Records processed    : 100
Matched              : 58
Exceptions           : 42
Match rate           : 58.00%
Accuracy (vs truth)  : 100.00%
False positives      : 0
False negatives      : 0
Precision            : 100.00%
Recall               : 100.00%
F1                   : 100.00%
Processing time      : 1.040 ms (report timer: 0.900 ms)
Throughput           : 96,107.64 records/sec
----------------------------------------------------------------
Exception breakdown:
  MATCHED                             : 0
  AMOUNT_MISMATCH                     : 6
  UNEXPLAINED_SETTLEMENT_DIFFERENCE   : 7
  MISSING_SETTLEMENT                  : 5
  MISSING_PAYMENT                     : 3
  DUPLICATE_SETTLEMENT                : 3
  CURRENCY_MISMATCH                   : 2
  INVALID_STATUS                      : 2
  REFUND_MISMATCH                     : 7
  SETTLEMENT_DELAY                    : 5
  UNRESOLVED                          : 2
----------------------------------------------------------------
Mismatches vs ground truth: 0
================================================================
RESULT               : PASS
```

(Timing numbers vary per machine; counts and rates are pinned.)

## 13. Limitations

Deliberate, documented non-goals of this iteration:

- **Two sources, not three.** Bank-statement ingestion is not wired in;
  the record model was chosen so a third source slots into the same
  rules.
- **Fee/tax are consumed, never modeled.** The engine reads `fee_minor`
  / `tax_minor` off the payment record and does no rate math; where the
  provider omits them, differences surface as legacy
  `AMOUNT_MISMATCH` rather than being silently approved.
- **No refund fee rebates.** Razorpay's fee handling on refunds is
  merchant-configuration-dependent and undocumented in this project, so
  a full refund leaves the expectation at −fee−tax (reported honestly,
  see tests) instead of guessing rebate behavior.
- **Timing needs configuration and clocks.** R9 runs only when callers
  supply `max_settlement_delay_days` *and* both records carry dates;
  there is no assumed payout schedule — none is documented by Razorpay.
- **Batch-level settlements.** Real `Settlement` rows aggregate many
  payments with no per-payment reference, so per-payment expectations
  come from payment-record components, not settlement-batch fees.
- **No fuzzy matching.** Attribution is exact-reference only; near-miss
  references become `MISSING_PAYMENT`/`UNRESOLVED` instead of guessed.
- **No auto-resolution.** Nothing mutates state or retries settlements;
  the loop reports, humans decide.
- **Synthetic data only.** Production wiring would feed real records into
  the same `reconcile()` signature; nothing else changes.

## 14. Known unresolved cases

Two shapes route to human review by design: **blank or whitespace-only
attribution references** on settlement lines (`UNRESOLVED`), and the
same shape on processed refunds (money moved but nothing says for which
payment). They anchor on their own record ids so a reviewer can act on
them, and they count in both `exception_count` and `unresolved_count`.
Everything else resolves to a definite terminal status — the loop never
emits "unknown"; per the master-spec taxonomy, an unrecognized pattern
would have to be added to the enum explicitly rather than smuggled
through an ad-hoc label.

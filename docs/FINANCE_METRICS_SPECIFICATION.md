# Finance Intelligence Engine — Metrics Specification

> **Architecture note:** ReconAgent operates on a normalized internal financial data model. Synthetic data is provided for deterministic demos and evaluation. External payment-provider ingestion is outside the core controller. This document is retained as **historical research provenance**: the `[Razorpay Fact]` citations below record where the original field semantics (status enums, subunit amounts, settlement behavior) were sourced from while designing that normalized model. No live provider API is called anywhere in this repository.

**Document status:** Draft v1.0 — SOURCE OF TRUTH for the deterministic calculation layer
**Scope:** Definitions, formulas, data sources, assumptions, edge cases. **No implementation code.**
**Project:** Razorpay AI Finance Controller (original working name; see architecture note above)

---

## How to read this document

Every metric and every field definition is labeled with one of these origin tags:

| Tag | Meaning |
|---|---|
| **[Razorpay Fact]** | Stated directly by official Razorpay documentation, with URL cited |
| **[Standard Formula]** | A generic accounting/mathematics convention, not Razorpay-specific |
| **[Project Definition]** | A choice our project is making — Razorpay does not define this |
| **[Assumption]** | Working assumption, not yet confirmed against a live account/sandbox |
| **NOT VERIFIED — DO NOT IMPLEMENT YET** | Could not be confirmed from official documentation in this research pass |

Every table row with a `Status` column uses one of: `VERIFIED`, `VERIFIED WITH ASSUMPTION`, `REQUIRES BUSINESS DECISION`, `NOT VERIFIED — DO NOT IMPLEMENT YET`. Nothing is marked `VERIFIED` unless a cited official Razorpay documentation page supports it.

**Research method:** All Razorpay-specific facts below were verified against pages under `razorpay.com/docs/` (official Razorpay documentation) during this research pass. Where the only available official page described a non-INR/regional variant of an entity (e.g., a `method` enum showing `card`/`ach` for a US-preferred-country page), this is explicitly flagged rather than presented as the India-account behavior.

---

## Part 1 — Razorpay Data Sources

### 1.1 Payments

- **Resource name:** Payment entity
- **Official documentation:** `https://razorpay.com/docs/api/payments/entity/`
- **What it represents [Razorpay Fact]:** A single payment attempt made by a customer, from creation through its final state.
- **Base URL:** `https://api.razorpay.com/v1/payments` **[Razorpay Fact]** (`https://razorpay.com/docs/api/`)

**Key fields [Razorpay Fact, from `payments/entity`]:**

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `id` | string | — | Unique identifier of the payment |
| `entity` | string | — | Always `"payment"` |
| `amount` | integer | smallest currency subunit (paise for INR) | Payment amount |
| `currency` | string | — | Currency of the payment |
| `status` | string | — | Lifecycle status. Documented possible values: `created`, `authorized`, `captured`, `refunded`, `failed` |
| `order_id` | string | — | Linked Order id, if the payment was made against an Order |
| `international` | boolean | — | Whether the payment was made via an international card |
| `refund_status` | string | — | `null`, `partial`, or `full` |
| `amount_refunded` | integer | smallest currency subunit | Amount refunded so far against this payment |
| `captured` | boolean | — | Whether the payment has been captured |
| `fee` | integer | smallest currency subunit | Fee charged by Razorpay |
| `tax` | integer | smallest currency subunit | Tax charged on the fee |
| `method` | string | — | Payment method used |
| `error_code`, `error_description`, `error_source`, `error_step`, `error_reason` | string | — | Populated only on failed payments |
| `created_at` | integer | Unix timestamp (seconds) | When the payment was created |

**Status values — [Razorpay Fact]:** `created`, `authorized`, `captured`, `refunded`, `failed` (source: `razorpay.com/docs/api/payments/entity/`).

**Method field — [NOT VERIFIED — DO NOT IMPLEMENT YET for exact India enum]:** The only official entity page returned by this research pass that lists `method`'s possible values (`card`, `ach`) is a non-INR/regional (US/Curlec-style) rendering of the docs and does not reflect an Indian account. Multiple official Razorpay marketing/product pages (`razorpay.com/docs/payments/payment-methods/`, `razorpay.com/docs/payments/optimizer/supported-gateways-aggregators/`) confirm that Indian accounts support **card, netbanking, wallet, UPI, and EMI** as payment modes, but no single official *API reference* page was fetched in this pass that enumerates the exact `method` field string values for an INR payment (commonly documented elsewhere as `card`, `netbanking`, `wallet`, `emi`, `upi`). **Do not hardcode a method enum until this is confirmed against the live/sandbox API response for an INR account.**

**Payment Life Cycle — [Razorpay Fact]:** Official page confirms a defined "Payment Life Cycle" state diagram exists (`razorpay.com/docs/payments/payments/`), and separately confirms: a payment with no bank response is shown as `Created`; after 10 minutes with no response it is marked `Failed` due to timeout; Razorpay then polls the bank for up to 3 days, and if a successful status is later received the payment moves to `Authorized` (this is called **late authorisation**) (`razorpay.com/docs/payments/payments/late-authorisation/`). By default, a captured payment happens automatically once the customer completes payment; payments can also remain in `authorized` state for late-authorization or manual-capture business reasons (`razorpay.com/docs/payments/dashboard/account-settings/capture-refund/`).

**Failure semantics — [Razorpay Fact]:** "A payment is said to be in the 'failed' state when we do not receive a successful callback message on the transaction from the issuing bank." If the customer's account was debited but no successful callback was received, the amount is auto-refunded by the issuing bank, typically within 7–10 working days (`razorpay.com/docs/payments/payments/faqs/`, `razorpay.com/docs/pos/payments/faqs/`).

**Pagination — [Razorpay Fact]:** `Fetch All Payments` (`razorpay.com/docs/api/payments/fetch-all-payments/`) supports `from`/`to` (Unix timestamp, seconds), `count` (default 10, **max 100**), and `skip`, used together for pagination.

### 1.2 Orders

- **Resource name:** Order entity
- **Official documentation:** `https://razorpay.com/docs/api/orders/entity/`, `https://razorpay.com/docs/api/orders/create/`
- **What it represents [Razorpay Fact]:** A container representing an intent to collect a payment (or one or more payment attempts) for a specific amount; not every payment needs to be tied to an Order, but Orders provide idempotency and support tracking multiple/partial payment attempts against one intended charge.

**Key fields [Razorpay Fact]:**

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `id` | string | — | Unique identifier of the order |
| `entity` | string | — | Always `"order"` |
| `amount` | integer | smallest currency subunit | Total order amount |
| `amount_paid` | integer | smallest currency subunit | Amount paid against the order so far |
| `amount_due` | integer | smallest currency subunit | Amount pending against the order |
| `currency` | string | — | ISO currency code |
| `receipt` | string | — | Merchant-supplied receipt reference, max 40 characters, must be unique |
| `status` | string | — | Order lifecycle status |
| `attempts` | integer | — | Number of payment attempts (successful and failed) made against this order |
| `notes` | object | — | Merchant-supplied key-value metadata |
| `created_at` | integer | Unix timestamp (seconds) | Order creation time |

**Order status values — [Razorpay Fact, from `orders/create`]:**
- `created` — order created, no payment attempted yet
- `attempted` — a payment has been attempted at least once; order remains in this state until one payment associated with it is captured
- `paid` — a payment has been successfully captured against the order; no further payment requests are permitted; **the order stays in `paid` even if the associated payment is later refunded** (this is an important reconciliation nuance — order status does not reflect refund state)

**Partial payment — [Razorpay Fact]:** Orders entity includes a boolean indicating whether the customer is permitted to pay the order amount in installments/partial payments (`razorpay.com/docs/api/orders/entity/`).

### 1.3 Refunds

- **Resource name:** Refund entity
- **Official documentation:** `https://razorpay.com/docs/api/refunds/entity/`
- **What it represents [Razorpay Fact]:** A refund transaction issued against a specific payment. A single payment may have multiple associated refund records (partial refunds).

**Key fields [Razorpay Fact]:**

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `id` | string | — | Unique identifier of the refund (e.g. `rfnd_...`) |
| `entity` | string | — | Always `"refund"` |
| `amount` | integer | smallest currency subunit | Refund amount |
| `currency` | string | — | Currency of the refund |
| `payment_id` | string | — | The payment this refund is issued against |
| `status` | string | — | `pending`, `processed`, or `failed` (see below) |
| `speed_requested` | string | — | Requested processing speed |
| `speed_processed` | string | — | Actual speed used to process the refund |
| `batch_id` | string | — | Populated if the refund was created as part of a bulk/batch upload |
| `acquirer_data` | object | — | Contains reference numbers (RRN/ARN/UTR) from the banking partner |
| `notes` | object | — | Merchant-supplied metadata |
| `created_at` | integer | Unix timestamp (seconds) | Refund creation time |

**Refund status values — [Razorpay Fact, from `refunds/entity`]:**
- `pending` — "Razorpay is attempting to process the refund." This can also be the state a caller sees synchronously even when the refund later settles normally (`razorpay.com/docs/payments/refunds/faqs/`: "a refund status can remain in a 'pending' state, particularly when the refund cannot be processed instantly").
- `processed` — the refund has been processed by the payment processing partner.
- `failed` — one documented cause: "Refund is not possible for a payment which is more than 6 months old."
- The `refund.processed` webhook is documented as "the most reliable and recommended way to receive the final status update (Processed, Failed or Reversed)" — meaning **a `reversed` outcome also exists** at the webhook/event level even though it isn't listed as a core `status` enum value on the entity page itself. **[NOT VERIFIED — DO NOT IMPLEMENT YET]** whether `reversed` can appear as the `status` field value on the Refund entity itself, versus only as a webhook event name — this needs confirmation against the Webhooks payload reference before the calculation layer treats it as a distinct refund status.

**Refund timing — [Razorpay Fact]:** Normal-speed refunds take 5–7 working days; instant refunds are near-immediate when available (`razorpay.com/docs/payments/refunds/?preferred-country=IN`, `razorpay.com/docs/payments/refunds/faqs/`).

**Payment ↔ Refund relationship — [Razorpay Fact]:** The Payment entity's `refund_status` (`null` / `partial` / `full`) and `amount_refunded` fields are the payment-level summary of all refunds issued against it; the Refund entity itself is the individual refund transaction record. Multiple `Refund` records can exist for one `payment_id` (partial refunds issued incrementally).

### 1.4 Settlements

- **Resource name:** Settlement entity
- **Official documentation:** `https://razorpay.com/docs/api/settlements/` (index), settlement examples confirmed via `razorpay-php/documents/settlement.md` (official Razorpay GitHub SDK docs, mirroring the API reference) and `razorpay.com/docs/api/settlements/fetch-recon/`
- **What it represents [Razorpay Fact]:** A batch payout from Razorpay to the merchant's bank account, aggregating multiple underlying transactions (payments, refunds, transfers, adjustments) settled together.

**Key fields on the standard Settlement entity [Razorpay Fact]:**

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `id` | string | — | Unique identifier of the settlement (e.g. `setl_...`) |
| `entity` | string | — | Always `"settlement"` |
| `amount` | integer | smallest currency subunit (paise) | Net amount settled to the bank account |
| `status` | string | — | e.g. `processed` |
| `fees` | integer | smallest currency subunit | Total fees deducted for this settlement |
| `tax` | integer | smallest currency subunit | Tax on the fee component |
| `utr` | string | — | Unique Transfer Reference from the banking partner |
| `created_at` | integer | Unix timestamp (seconds) | Settlement creation time |

**Settlement Recon (line-item) API — [Razorpay Fact, critical for reconciliation]:**

`GET /v1/settlements/recon/combined?year=yyyy&month=mm[&day=dd]` (`razorpay.com/docs/api/settlements/fetch-recon/`)

> "Use this endpoint to return a list of all transactions such as payments, refunds, transfers and adjustments settled to your account on a particular day or month."

This is the **transaction-level breakdown of a settlement** — it is the mechanism by which individual payments/refunds/transfers/adjustments can be tied back to a specific `settlement_id`. Response items include:

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `entity_id` | string | — | ID of the underlying transaction (payment/refund/transfer/adjustment) that was settled |
| `type` | string | — | `payment`, `refund`, `transfer`, or `adjustment` |
| `debit` | integer | smallest currency subunit | Amount debited from the account for this line item |
| `credit` | integer | smallest currency subunit | Amount credited to the account for this line item |
| `amount` | integer | smallest currency subunit | Total amount debited or credited |
| `currency` | string | — | 3-letter ISO code |
| `fee` | integer | smallest currency subunit | Fee charged for this specific transaction |
| `tax` | integer | smallest currency subunit | Tax on that fee |
| `on_hold` | boolean | — | Whether settlement for a transfer is on hold |
| `settled` | boolean | — | Whether this transaction has been settled |
| `created_at` | integer | Unix timestamp (seconds) | When the underlying transaction was created |
| `settled_at` | integer | Unix timestamp (seconds) | When it was settled |
| `settlement_id` | string | — | The settlement batch this line item belongs to |
| `settlement_utr` | string | — | UTR of the settlement batch |
| `payment_id` | string | — | For `refund`/`transfer` rows, the parent payment; `null` for `payment` rows |
| `order_id`, `order_receipt` | string | — | Order linkage, if applicable |
| `method`, `card_network`, `card_issuer`, `card_type` | string | — | Payment method detail |
| `dispute_id` | string | — | Linked dispute, if any |

This endpoint materially changes what is calculable: **individual payments, refunds, transfers, and adjustments settled within a given day/month can be enumerated and tied to a specific settlement batch and its fee/tax breakdown**, via official API data — not just an aggregate settlement total.

**Settlement cycle / timing — [Razorpay Fact]:**
- Default settlement cycle: **T+2 working days** for domestic (INR) payments, **T+7 working days** for international payments, where T = date of payment capture (`razorpay.com/docs/payments/settlements/faqs/`, settlement API docs mirrored at `docs.globepayinc.com/api/settlements/` which republishes Razorpay's own documented cycle language).
- The schedule is **subject to bank approval and can vary by business vertical, risk factors, etc.** (`razorpay.com/docs/payments/settlements/`) — i.e., **T+2 is a documented default, not a guarantee for every merchant.**
- "Working days" **exclude** second/fourth Saturdays, Sundays, and bank holidays (`razorpay.com/docs/payments/settlements/faqs/`).
- **Partial settlements can occur:** if the live balance at the scheduled settlement time is less than the amount otherwise due (e.g., because a refund reduced the balance), Razorpay settles only what fits the current live balance and defers the remainder to the next scheduled settlement (`razorpay.com/docs/payments/settlements/`, with a worked example).
- Settlement currency is **always INR**, regardless of the currency the customer paid in (`razorpay.com/docs/payments/international-payments/faqs/`).

**Settlement currency — [Razorpay Fact]:** "The Settlement currency is INR (Indian rupees) for all transactions made using Razorpay. Thus, international payments are settled in INR."

### 1.5 Payment Life Cycle vs. Order vs. Settlement — relationship summary [Project Definition, built from the above facts]

```
Order (optional container, tracks amount_paid/amount_due/attempts)
   ↓
Payment (created → authorized → captured → [refunded] / failed)
   ↓ (if captured)
Settlement (batch payout to bank, T+2 default, contains this payment as a recon line item)
   ↓ (if a refund is later issued)
Refund (deducted from a future settlement batch as a `debit` recon line item)
```

This chain is our project's synthesis of the individually-documented facts above, not a single Razorpay-published diagram literally titled this way.

---

## Part 2 — Payment Metrics

### 2.0 Status Classification — Foundational Decision

**[Project Definition, built strictly from Razorpay's documented status set]**

Razorpay's documented Payment `status` values are: `created`, `authorized`, `captured`, `refunded`, `failed` (source: Section 1.1).

| Status | Classified as | Reasoning |
|---|---|---|
| `created` | **Neither success nor failure — in-progress** | No bank response yet; must not be counted as failed or successful |
| `authorized` | **Neither success nor failure — in-progress / pending action** | Funds authorized but not yet captured; per documentation, may resolve to `captured` or be auto-refunded if never captured within the capture window. Counting this as "successful revenue" would be premature since capture (and therefore settlement eligibility) has not occurred. |
| `captured` | **Successful** | Payment fully completed and eligible for settlement |
| `refunded` | **Successful payment, subsequently reversed** | The underlying payment *was* successfully captured (this status is reached only after capture); it is not a "failed" transaction, but it is no longer "active revenue." Handled distinctly in Part 3 (Revenue) and Part 4 (Refunds), not folded into either Success Rate or Failure Rate silently. |
| `failed` | **Failed** | Payment did not complete |

**Explicit inclusion/exclusion rule for Payment Metrics (Part 2):**
- **"Successful"** for the purposes of Transaction Volume/Success Rate metrics = `status == "captured"` **[Project Definition]**. Razorpay does not itself declare "which statuses count as business success" — that is inherently a project-level classification decision, made here as conservatively as possible (only a fully captured, settlement-eligible payment counts as successful).
- **"Failed"** = `status == "failed"` **[Project Definition]**, directly matching Razorpay's own terminal failure state.
- `created` and `authorized` are **excluded from both** the success and failure counts in the base Success/Failure Rate metrics, and tracked separately as **"in-progress"** — because counting them either way would misrepresent an incomplete transaction as a completed outcome. This is called out explicitly per the task instructions: *do not assume every status is success/failure*.
- `refunded` payments are **included in Successful Transaction Count** (they were captured) but are tracked with a separate refund-adjusted view in Part 3 — a refunded payment is not a "failed" transaction in Razorpay's own status model, and conflating it with `failed` would misstate what actually happened at the payment-gateway level.

### 2.1 Transaction Volume

- **Definition [Project Definition]:** Total number of payment attempts recorded in the selected time period, regardless of outcome.
- **Required fields:** `id`, `created_at`
- **Formula:** `Transaction Volume = COUNT(payments WHERE created_at BETWEEN period_start AND period_end)`
- **Filtering:** No status filter — includes `created`, `authorized`, `captured`, `refunded`, `failed`.
- **Time period:** Explicit start/end, using `created_at` (Unix timestamp, seconds) per Razorpay's documented field.
- **Example:** 4 payment records in a day → Transaction Volume = 4.
- **Edge cases:** Zero records → Transaction Volume = 0 (not null, not an error).
- **Source:** Field existence and semantics — [Razorpay Fact], `razorpay.com/docs/api/payments/entity/`. Formula itself — [Project Definition] (a simple count; Razorpay does not define "Transaction Volume" as a named metric).
- **Status:** `VERIFIED`

### 2.2 Successful Transaction Count

- **Definition [Project Definition]:** Number of payments with `status == "captured"` in the period (per Section 2.0's classification decision), inclusive of payments that were later refunded (see note below).
- **Required fields:** `status`, `created_at`
- **Formula:** `Successful Transaction Count = COUNT(payments WHERE status == "captured" AND created_at BETWEEN period_start AND period_end)`
- **Filtering:** `status == "captured"`. **Note:** Because Razorpay does not remove or change the payment's terminal status back from `captured` merely because a refund was later issued against it (refund state lives on `refund_status`/`amount_refunded`, and is also separately observable as the `refunded` status value in some contexts), the project must decide whether a **fully refunded** payment still counts here.
- **This exact ambiguity is flagged:** **REQUIRES BUSINESS DECISION** — see Part 3.6.
- **Example:** Payments A (₹1,000, captured), B (₹2,000, captured), D (₹3,000, captured) → Successful Transaction Count = 3.
- **Edge cases:** A payment stuck in `authorized` (never captured, never failed) is excluded — it is neither success nor failure.
- **Status:** `VERIFIED WITH ASSUMPTION` (verified that `captured` is the correct status to filter on; assumption is that refunded-but-originally-captured payments are included here — see 3.6 for the revenue-level ambiguity this creates).

### 2.3 Failed Transaction Count

- **Definition [Project Definition]:** Number of payments with `status == "failed"` in the period.
- **Formula:** `Failed Transaction Count = COUNT(payments WHERE status == "failed" AND created_at BETWEEN period_start AND period_end)`
- **Example:** Payment C (₹500, failed) → Failed Transaction Count = 1.
- **Edge case — late authorisation:** A payment that was marked `failed` due to a timeout but later receives a delayed successful bank response moves to `authorized` (per `razorpay.com/docs/payments/payments/late-authorisation/`). **This means a payment's status is not permanently fixed at the moment of ingestion for up to 3 days after creation** — a naive one-time snapshot of "failed" counts can overcount failures if a late authorization arrives after the metric was computed. This must be handled by either (a) re-fetching/re-syncing payment status for a rolling window (e.g., re-check payments created in the last 3–5 days), or (b) accepting a documented margin of error for same-day/next-day reporting. **[Razorpay Fact — late authorization mechanism]; [Project Definition — how we choose to handle it, REQUIRES BUSINESS DECISION on re-sync window]**.
- **Status:** `VERIFIED WITH ASSUMPTION` (status semantics verified; re-sync/timing handling requires a business decision — see Part 6 and Part 8).

### 2.4 Success Rate

- **Definition [Standard Formula], applied to Razorpay-classified statuses [Project Definition]:**
```
Success Rate (%) = (Successful Transaction Count / Transaction Volume) × 100
```
- **Filtering:** Uses the same period and the same `captured` classification as 2.2.
- **Denominator choice — REQUIRES BUSINESS DECISION:** Should the denominator be **all** transaction attempts (`Transaction Volume`, including `created`/`authorized` in-progress ones), or only **decisioned** attempts (`captured` + `failed`, excluding still-in-progress ones)? Both are defensible:
  - Using `Transaction Volume` (all attempts) is the safer default for a point-in-time report, since in-progress payments will eventually resolve one way or another and excluding them changes the denominator retroactively.
  - Using only decisioned attempts (`captured` + `failed`) better represents "of the attempts that concluded, how many succeeded," which is closer to how payment gateways commonly report success rate for monitoring purposes.
- **Recommendation for this project [Project Definition]:** Use `Transaction Volume` (all attempts in the period) as the denominator for the primary dashboard metric, since it is simpler, more conservative, and avoids silently dropping records. Offer the decisioned-only variant as a secondary/advanced metric if needed later.
- **Zero-denominator handling:** If `Transaction Volume == 0`, Success Rate is **undefined**, not `0%`. See Part 6.4 for the exact API behavior.
- **Status:** `REQUIRES BUSINESS DECISION` (formula math is standard; denominator choice is not yet finalized)

### 2.5 Failure Rate

- **Formula [Standard Formula]:**
```
Failure Rate (%) = (Failed Transaction Count / Transaction Volume) × 100
```
- Same denominator question and zero-denominator handling as Success Rate (2.4). **Note: Success Rate + Failure Rate will NOT sum to 100% when in-progress (`created`/`authorized`) transactions exist in the period** — this is expected and must be documented in the API response, not treated as a bug.
- **Status:** `REQUIRES BUSINESS DECISION` (same reason as 2.4)

### 2.6 Successful Payment Amount / Revenue

- Covered in full under Part 3 (Revenue Metrics) below, since "revenue" requires the additional refund/fee context that Part 2 does not need.

---

## Part 3 — Revenue Metrics

> **Explicit warning per task instructions:** "Gross Revenue," "Successful Revenue," and "Net Revenue" are **not** universally standardized accounting terms — different businesses and different payment processors use them differently. Nothing here should be read as Razorpay's own definition of these terms; Razorpay does not publish a "Gross Revenue" or "Net Revenue" metric definition. All formulas below are **[Project Definition]**, built only from Razorpay-documented fields.

### 3.1 Gross Revenue — Our Project's Definition

- **Definition [Project Definition]:** The total `amount` of all **captured** payments in the period, before any deduction for Razorpay fees, tax, or refunds.
- **Formula:**
```
Gross Revenue = SUM(payment.amount WHERE payment.status == "captured" AND created_at IN period)
```
- **Unit:** Smallest currency subunit (paise for INR) at calculation time; convert to major currency unit (₹) only at presentation layer.
- **Why this definition:** `amount` on a captured payment is the amount the customer was actually charged (Razorpay Fact: "Payment amount in the smallest currency sub-unit"), independent of Razorpay's fee. This is the most literal, unambiguous "top-line" figure obtainable directly from the Payment entity.
- **Status:** `VERIFIED WITH ASSUMPTION` (field semantics verified; "Gross Revenue" as a named business concept is our label, not Razorpay's)

### 3.2 Successful Revenue

- **[Project Definition]:** In this specification, **Successful Revenue is treated as identical to Gross Revenue** (3.1) — the sum of `amount` for `captured` payments in the period. We introduce no separate formula for it to avoid two names for the same number. If the team later wants "Successful Revenue" to mean something distinct from "Gross Revenue" (e.g., excluding certain payment methods), that must be an explicit, separately-approved business decision.
- **Status:** `REQUIRES BUSINESS DECISION` (should this term exist as distinct from Gross Revenue at all? Recommendation: no, until a concrete reason is identified.)

### 3.3 Refund Amount

- **Definition [Project Definition, built on Razorpay Fact fields]:** Total amount refunded in the period.
- **Formula (two valid framings — see below):**
  - **(a) By refund creation date:** `SUM(refund.amount WHERE refund.status == "processed" AND refund.created_at IN period)`
  - **(b) By original payment's refunded amount:** `SUM(payment.amount_refunded WHERE payment.created_at IN period)` — attributes the refund back to the period the *original sale* occurred in, not when the refund happened.
- **These give different numbers for any period boundary crossed by a payment-then-later-refund pair.** This is exactly the kind of ambiguity the task requires us to flag.
- **Recommendation for this project [Project Definition]:** Use framing (a) — refund amount attributed to the period **in which the refund itself was created/processed** — because this matches how cash actually moves out of the account and matches how Razorpay's own settlement recon data (Section 1.4) ties refunds to specific settlement dates, not original payment dates.
- **Status filtering:** Only `status == "processed"` refunds should count toward *realized* Refund Amount. `pending` refunds are a known future cash outflow but have not yet moved; `failed` refunds did not move money at all and must be excluded.
- **Status:** `REQUIRES BUSINESS DECISION` (which period-attribution framing to use — recommendation given above, but not yet a finalized decision)

### 3.4 Net Revenue

- **[Project Definition — REQUIRES BUSINESS DECISION, not silently assumed]:**

Multiple valid definitions exist depending on what "net" is meant to net out:

| Candidate Definition | Formula | What it represents |
|---|---|---|
| **Net of refunds only** | `Gross Revenue − Refund Amount` | Revenue actually retained by the business after returning money to customers, but *before* Razorpay's own processing fees |
| **Net of fees only** | `Gross Revenue − Razorpay Fee (SUM(payment.fee) + SUM(payment.tax))` | Revenue after Razorpay's cut, but before accounting for refunds |
| **Net of both refunds and fees** | `Gross Revenue − Refund Amount − SUM(payment.fee) − SUM(payment.tax)` | Closest to "cash actually available to the business," but conflates two different kinds of deduction (a customer-driven reversal vs. a processor's service charge) into one number |

**Recommendation for this project [Project Definition]:** Do **not** default to any single one of these silently. Expose all three as **separately labeled** fields (`netOfRefunds`, `netOfFees`, `netOfRefundsAndFees`) in the API response (see Part 12) rather than picking one and calling it "Net Revenue" unqualified. This avoids the exact failure mode the task calls out: silently choosing a definition.

- **Status:** `REQUIRES BUSINESS DECISION` — **"REQUIRES BUSINESS DEFINITION BEFORE IMPLEMENTATION"** for any single unqualified "Net Revenue" field. The three sub-components above are individually `VERIFIED WITH ASSUMPTION` once 3.1 and 3.3's own open decisions are resolved.

### 3.5 Fee and Tax — supporting figures

- **Razorpay Fee [Razorpay Fact field, Project Definition aggregation]:** `SUM(payment.fee WHERE status == "captured" AND created_at IN period)` — `fee` is documented as "Fee charged by Razorpay," in the same subunit as `amount`.
- **Razorpay Tax [Razorpay Fact field, Project Definition aggregation]:** `SUM(payment.tax WHERE status == "captured" AND created_at IN period)` — "Tax charged for the payment," i.e., tax on Razorpay's fee (GST on the platform fee, consistent with third-party pricing explainers, though the entity page itself only labels it "Tax charged for the payment" without naming GST specifically — treat the GST characterization as **[Assumption]**, not confirmed on the entity page itself).
- **Status:** `VERIFIED WITH ASSUMPTION`

### 3.6 The Refunded-Payment Double-Counting Question — REQUIRES BUSINESS DECISION

This is flagged explicitly because it affects both Part 2 and Part 3:

> If Payment X (₹10,000, `status: captured`, later `refund_status: full`, `amount_refunded: 10000`) exists, should it be counted in **Successful Transaction Count** (2.2) and **Gross Revenue** (3.1)?

Two defensible answers:
1. **Yes, include it** in gross figures, and let Net Revenue (3.4, "net of refunds") be the place where the refund is subtracted back out. This preserves an accurate picture of "what was actually sold" separately from "what was kept."
2. **No, exclude fully-refunded payments** from Successful/Gross figures entirely, on the theory that a fully-reversed sale isn't "revenue" in any meaningful sense.

**Recommendation for this project [Project Definition]:** Option 1 (include in gross, net out via Net Revenue). This is the more standard accounting posture (gross vs. net presentation) and avoids retroactively rewriting a "Successful Transaction Count" that already shipped in a prior report the moment a refund happens days or weeks later.

**Status:** `REQUIRES BUSINESS DECISION` (recommendation given, not yet approved)

---

## Part 4 — Refund Metrics

### 4.1 Refund Count

- **Formula:** `Refund Count = COUNT(refunds WHERE status == "processed" AND created_at IN period)`
- **Status:** `VERIFIED WITH ASSUMPTION` (status field verified; whether `pending` refunds should also be counted in a separate "requested" metric is a secondary, optional decision — not required for MVP)

### 4.2 Refund Amount

- Defined in 3.3 above.

### 4.3 Refund Rate — Multiple Valid Definitions

The task explicitly warns against defaulting to `Refund Amount / Successful Revenue × 100` without investigation. Candidate definitions:

| Candidate | Formula | What it measures | Trade-off |
|---|---|---|---|
| **(A) Amount-based, against Gross Revenue** | `Refund Amount / Gross Revenue × 100` | What fraction of money taken in was later given back | Simple, matches common e-commerce reporting; conflates high-value and low-value refunds equally weighted by ₹, which is arguably correct for a *financial* (as opposed to *operational*) metric |
| **(B) Count-based, against Successful Transaction Count** | `Refund Count / Successful Transaction Count × 100` | What fraction of *transactions* resulted in a refund, regardless of amount | Better for operational/quality monitoring (e.g., "1 in 20 orders gets refunded"), but can be misleading financially — one giant refund and 100 tiny ones would look identical to 100 giant refunds and one tiny one if only counting |
| **(C) Amount-based, against period-matched revenue only** | `Refund Amount (this period) / Gross Revenue (same period)  × 100` | Same as (A) but explicit that both sides use the same period's transactions, not cross-period matching | Avoids conflating a refund against an old sale with this period's new sales in the denominator |

**Recommendation for this project [Project Definition]:** Use **(A)** — `Refund Amount / Gross Revenue × 100`, both computed over the **same reporting period**, using the period-of-refund attribution chosen in 3.3. This is a **financial** metric (this document's scope), so amount-weighting is more appropriate than a flat per-transaction count; a separate **operational** refund-count-based metric (B) can be added later for support/quality-ops dashboards but is out of scope for the Finance Intelligence Engine's core output.

- **Status:** `REQUIRES BUSINESS DECISION` (recommendation given: definition A; not yet approved)

### 4.4 Payment ↔ Refund Relationship, Partial Refunds, Multiple Refunds

- **[Razorpay Fact]:** A single payment can have `refund_status` = `partial` (some but not all of `amount` refunded) or `full` (`amount_refunded == amount`). Multiple individual `Refund` records can exist against the same `payment_id` (e.g., two separate partial refunds that together reach `full`).
- **Implication for calculation logic [Project Definition]:** Refund aggregation must sum **individual Refund entity records** (Part 4.1/4.2), not just read the payment's `amount_refunded` snapshot, if the goal is period-accurate refund attribution — because `amount_refunded` on the Payment entity is a **cumulative, current-state** number that does not tell you *when* each partial refund happened. Relying on `amount_refunded` alone would make it impossible to correctly attribute a refund to the period it actually occurred in (this reinforces the framing (a) recommendation in 3.3).

### 4.5 Refund Timestamps

- **[Razorpay Fact]:** `Refund.created_at` is the authoritative timestamp for "when the refund was created" (Unix seconds). The Settlement Recon line-item API (Section 1.4) separately provides `settled_at` for when that refund actually left the account balance. **These are two different, both-legitimate timestamps** — "refund created" vs. "refund settled/debited" — and the project must choose which one drives period attribution for financial reporting (created_at is recommended, as it reflects when the refund obligation was recognized, consistent with normal accrual-style reporting).

---

## Part 5 — Settlement Metrics

> This section is treated with the highest caution per the task's explicit instruction not to assume `Expected Settlement = Payments − Refunds` without verifying it against both documentation and the actual data model.

### 5.1 What a Settlement Represents — [Razorpay Fact, Section 1.4]

A Settlement is a batch payout aggregating multiple underlying transactions (payments, refunds, transfers, adjustments) into one bank transfer, identified by `settlement_id` and a bank-provided `utr`.

### 5.2 Settlement Amount, Status, Fees, Tax — [Razorpay Fact, Section 1.4]

Directly available on the Settlement entity: `amount` (net settled), `status`, `fees`, `tax`, `utr`, `created_at`.

### 5.3 Settlement Items (line-level detail) — [Razorpay Fact, Section 1.4]

**This is the key finding of this research pass:** the `GET /v1/settlements/recon/combined` endpoint **does** provide transaction-level detail tying individual payments/refunds/transfers/adjustments to a specific `settlement_id`, each with its own `fee`, `tax`, `debit`/`credit`, `created_at`, and `settled_at`. This is a materially better data source than the plain Settlement entity alone for reconciliation purposes.

### 5.4 Refund Adjustments and Other Adjustments in Settlements — [Razorpay Fact]

The recon line-item `type` field explicitly includes `refund` and `adjustment` as distinct line-item types alongside `payment` and `transfer` — meaning refunds and miscellaneous adjustments are **debited out of a settlement batch** as their own line items, not silently netted invisibly into the settlement's top-line `amount`. `adjustment` line items carry a free-text `description` (e.g., `"test reason"` in the documented example) but **no further structured adjustment-reason taxonomy is documented** — meaning the *reason* for an adjustment is not machine-classifiable from the API alone beyond free text.

### 5.5 Settlement Timing — [Razorpay Fact, Section 1.4]

Default T+2 (domestic) / T+7 (international) working days from capture date, subject to bank approval and merchant-specific variation, with partial settlements possible when live balance is insufficient at the scheduled time.

### 5.6 Relationship Between Payments and Settlements — [Razorpay Fact]

Established via the recon line-item's `settlement_id` (for payments/refunds/transfers/adjustments that have been settled) — see 5.3. A **captured but not-yet-settled** payment will simply not yet appear in any recon line-item response; there is no separate "pending settlement" flag documented on the Payment entity itself beyond inferring it from the *absence* of a matching recon line item as of "now."

### 5.7 "Expected Settlement" — Investigated in Depth

**Question posed by the task:** Can `Expected Settlement = Payments − Refunds` be assumed?

**Finding:** **No — not as a simple two-term formula.** Based on the documented settlement mechanics (Section 1.4/5.5), an accurate "expected settlement" for a given settlement batch would need to account for **at minimum**:

1. Captured payment amounts within the relevant capture window
2. Razorpay's `fee` + `tax` for each of those payments (settlement `amount` is net of fees, not gross)
3. Any refunds processed against the merchant's balance within the same settlement window (debited before the payout, per the documented partial-settlement mechanics)
4. Any `transfer` line items (Route/marketplace splits, if used)
5. Any `adjustment` line items (free-text-reasoned, not fully structured/predictable in advance)
6. The **working-day** settlement calendar (excluding weekends, 2nd/4th Saturdays, and bank holidays) to determine which capture date maps to which settlement date
7. Possible **partial settlement carry-forward** if the live balance was insufficient on the originally scheduled date (Section 5.5) — meaning a payment captured on day T is not guaranteed to settle exactly on T+2; some of it could roll to T+3 or later depending on balance at settlement time

Because factors (4), (5), and (7) are either merchant-configuration-dependent, not fully predictable from payment data alone, or explicitly documented as subject to partial deferral, we conclude:

> **"Expected settlement cannot currently be determined with sufficient confidence from the available data, for the general case (a merchant using Route/transfers, or one that has ever had a partial-settlement event)."**

**However**, for the **common case of a merchant with no Route/transfers and a settlement balance that never falls short (no partial-settlement events)**, a *reasonable estimate* is calculable as:

```
Estimated Settlement (for payments captured on date T)
  = SUM(payment.amount − payment.fee − payment.tax
        WHERE payment.status == "captured" AND capture_date == T)
    − SUM(refund.amount WHERE refund.status == "processed"
          AND refund.created_at falls within the same settlement cycle)
```
mapped to the expected settlement date via the merchant's documented settlement cycle (default T+2 working days, adjusted for the working-day calendar).

**This is explicitly an ESTIMATE, not a guarantee**, and must be labeled as such in any API response (see Part 12) — e.g. `"estimatedSettlement"`, never `"expectedSettlement"` presented as fact.

**Safer alternative metric [Project Definition, recommended]:** Rather than trying to *predict* a future settlement amount, compute **"Settlement Reconciliation Status"** retrospectively, once actual settlement recon data (Section 5.3) is available:

```
Settlement Gap (for a given settlement_id)
  = (SUM of recon line-item `credit` amounts for that settlement_id)
    − (SUM of recon line-item `debit` amounts for that settlement_id)
    − Settlement.amount (the actual net amount reported on the Settlement entity)
```

If this gap is non-zero beyond a small rounding tolerance, it indicates a genuine reconciliation exception worth investigating — and unlike a *predicted* expected-settlement figure, this uses only data that Razorpay has already finalized and reported (both the settlement total and its own line items), so it does not depend on guessing fee/adjustment/timing behavior in advance.

- **Status:** `NOT VERIFIED — DO NOT IMPLEMENT YET` for any forward-looking "Expected Settlement" prediction metric as a trustworthy, presented-as-fact figure. `VERIFIED WITH ASSUMPTION` for the retrospective "Settlement Gap" comparison metric (5.7, safer alternative), since it relies only on already-fetched, already-finalized Razorpay data (Settlement entity `amount` vs. recon line items for that same `settlement_id`).

---

## Part 6 — Time-Based Trends

### 6.1 Period Definitions

| Period | Current | Previous |
|---|---|---|
| Today vs Yesterday | `[start_of_today, now)` | `[start_of_yesterday, start_of_today)` |
| This Week vs Previous Week | `[start_of_this_week, now)` | `[start_of_last_week, start_of_this_week)` |
| This Month vs Previous Month | `[start_of_this_month, now)` | `[start_of_last_month, start_of_this_month)` |

**[Project Definition]** — Razorpay does not define business "day/week/month" boundaries; this is purely a project/reporting-layer decision.

### 6.2 Timezone — REQUIRES BUSINESS DECISION

- **[Razorpay Fact]:** All Razorpay timestamps (`created_at`, `settled_at`, etc.) are Unix timestamps in seconds — i.e., **UTC-based epoch integers**, timezone-naive by construction (`razorpay.com/docs/api/payments/entity/` and consistently across every entity fetched in this research pass).
- **[Project Definition — REQUIRES BUSINESS DECISION]:** Since the business operates in India, "today," "this week," and "this month" almost certainly should be interpreted in **IST (UTC+5:30)** for finance-team-facing reporting (a financial day boundary at UTC midnight would split IST business days awkwardly around 5:30 AM IST). This must be an explicit, confirmed decision before implementation — do not silently default to UTC day boundaries when converting Unix timestamps to calendar dates.
- **Recommendation:** Normalize all `created_at`/`settled_at` Unix timestamps to IST before computing any calendar-day/week/month bucket, and document this normalization prominently in code and API responses (e.g., an explicit `"timezone": "Asia/Kolkata"` field on every trend response).
- **Status:** `REQUIRES BUSINESS DECISION`

### 6.3 Percentage Change

- **Formula [Standard Formula]:**
```
Percentage Change (%) = (Current − Previous) / Previous × 100
```
- **Absolute Change:** `Current − Previous` (always defined, including when `Previous == 0`).

### 6.4 Zero-Denominator Case — Explicit Handling Required

- **Case: `Previous == 0`, `Current > 0`** (e.g., Previous = 0, Current = 100): Percentage Change is **mathematically undefined** (division by zero), **not** "∞%" and **not** silently `0%`.
- **[Project Definition — recommended API behavior]:**
  - The API must **not** return a numeric `percentageChange` value in this case.
  - Instead return `"percentageChange": null` alongside an explicit `"changeType": "new_activity"` (or equivalent) flag, plus the raw `absoluteChange` value, which remains well-defined (`100 − 0 = 100`).
  - This applies identically to `Previous == 0, Current == 0` (no change, no activity in either period) — return `"percentageChange": null, "changeType": "no_activity"`, not `0%` (0% implies "measured, and found unchanged," which is a different claim than "nothing to compare").
- **Case: `Previous == 0, Current < 0`** — not physically meaningful for the metrics in this document (amounts/counts are never negative in the current scope), so no handling is defined for it; flagged for future consideration only if signed adjustment amounts are added to scope.
- **Status:** `VERIFIED` (this is a standard, unambiguous mathematical handling requirement, not a Razorpay-specific fact)

---

## Part 7 — Master Metric Definitions Table

| Metric | Definition | Formula | Data Source | Razorpay Fields | Project Rule? | Confidence | Status |
|---|---|---|---|---|---|---|---|
| Transaction Volume | All payment attempts in period | `COUNT(payments)` | Payments API | `id`, `created_at` | No | High | VERIFIED |
| Successful Transaction Count | Captured payments in period | `COUNT(payments WHERE status="captured")` | Payments API | `status`, `created_at` | Yes (status→success mapping) | High | VERIFIED WITH ASSUMPTION |
| Failed Transaction Count | Failed payments in period | `COUNT(payments WHERE status="failed")` | Payments API | `status`, `created_at` | Yes (status→failure mapping) | Medium (late-authorization re-sync issue) | VERIFIED WITH ASSUMPTION |
| Success Rate | % of attempts that captured | `Successful / Denominator × 100` | Payments API | `status` | Yes (denominator choice) | Medium | REQUIRES BUSINESS DECISION |
| Failure Rate | % of attempts that failed | `Failed / Denominator × 100` | Payments API | `status` | Yes (denominator choice) | Medium | REQUIRES BUSINESS DECISION |
| Gross Revenue | Sum of captured payment amounts | `SUM(amount WHERE status="captured")` | Payments API | `amount`, `status` | Yes (naming/scope) | High | VERIFIED WITH ASSUMPTION |
| Successful Revenue | = Gross Revenue in this spec | Same as Gross Revenue | Payments API | `amount`, `status` | Yes (may be redundant) | Medium | REQUIRES BUSINESS DECISION |
| Refund Amount | Sum of processed refunds in period | `SUM(refund.amount WHERE status="processed")` | Refunds API | `amount`, `status`, `created_at` | Yes (period attribution) | Medium | REQUIRES BUSINESS DECISION |
| Net Revenue (net of refunds) | Gross − Refunds | `Gross Revenue − Refund Amount` | Payments + Refunds API | `amount`, `refund.amount` | Yes | Medium | REQUIRES BUSINESS DECISION |
| Net Revenue (net of fees) | Gross − Razorpay fee/tax | `Gross Revenue − SUM(fee) − SUM(tax)` | Payments API | `fee`, `tax` | Yes | Medium | REQUIRES BUSINESS DECISION |
| Net Revenue (net of both) | Gross − Refunds − Fees | Combination of above | Payments + Refunds API | `amount`, `fee`, `tax`, `refund.amount` | Yes | Medium | REQUIRES BUSINESS DECISION |
| Refund Count | Count of processed refunds | `COUNT(refunds WHERE status="processed")` | Refunds API | `status`, `created_at` | No | High | VERIFIED WITH ASSUMPTION |
| Refund Rate | Refund Amount / Gross Revenue | `Refund Amount / Gross Revenue × 100` | Payments + Refunds API | `amount`, `refund.amount` | Yes (definition choice among 3 candidates) | Medium | REQUIRES BUSINESS DECISION |
| Settlement Amount (actual) | Net amount of a settlement batch | Direct field | Settlements API | `amount`, `fees`, `tax` | No | High | VERIFIED |
| Settlement Line Items | Transactions within a settlement | Direct list | Settlement Recon API | `entity_id`, `type`, `debit`, `credit`, `settlement_id` | No | High | VERIFIED |
| Expected/Predicted Settlement | Forward-looking settlement prediction | See 5.7 | Payments + Refunds API | `amount`, `fee`, `tax`, `refund.amount` | Yes | Low | NOT VERIFIED — DO NOT IMPLEMENT YET |
| Settlement Gap (retrospective) | Recon line items vs. reported settlement total | See 5.7 | Settlements + Settlement Recon API | `amount`, recon `credit`/`debit` | Yes | High | VERIFIED WITH ASSUMPTION |
| Today vs Yesterday / trend deltas | Absolute + % change | See Part 6 | Derived from above | `created_at` (timezone-normalized) | Yes (timezone) | Medium | REQUIRES BUSINESS DECISION |

---

## Part 8 — Edge Cases

| Edge Case | Handling |
|---|---|
| No transactions in period | All counts = 0, all sums = 0, all rates = `null` with `changeType: "no_activity"` (not divide-by-zero errors, not silently omitted fields) |
| Zero revenue | Gross Revenue = 0 is a valid, real value — distinct from "no data available" |
| Failed payments | Excluded from Gross Revenue and Successful Transaction Count; included in Transaction Volume and Failed Transaction Count |
| Partially refunded payments | Included in Gross Revenue at full original `amount` (per 3.6 recommendation); the refunded portion appears in Refund Amount and reduces Net Revenue, not Gross Revenue |
| Fully refunded payments | Same treatment as partial — included in gross, netted out via Net Revenue (3.6) |
| Multiple refunds against one payment | Sum all individual `Refund` records for that `payment_id`; do not rely solely on the Payment entity's cumulative `amount_refunded` for period-accurate attribution (4.4) |
| Duplicate records (e.g., re-ingested webhook events) | Deduplicate by `id` (payment/refund/settlement IDs are unique per Razorpay) before aggregation; **implementation detail deferred**, but the rule (dedupe by entity ID) is specified now so it is not silently forgotten |
| Cancelled/expired orders | An Order that never reaches `paid` status contributes 0 to revenue; its `attempts` count is informational only and is not itself a payment metric |
| Payment status changes after initial fetch (late authorization) | See 2.3 — requires a re-sync window; do not treat a single point-in-time fetch as final for payments created in the last 3–5 days |
| Missing fields | If a documented-required field is absent/null in an API response, exclude that record from the affected metric and log it — do not default to 0 silently for a financial amount field |
| Invalid amounts (negative, non-integer where integer expected) | Reject/flag the record; Razorpay's own amount fields are documented as non-negative integers in the smallest currency subunit, so a negative or non-integer value indicates either an ingestion bug or an out-of-spec upstream response, not a valid business case within this project's documented scope |
| Negative adjustments | The Settlement Recon `adjustment` type can itself represent a negative-to-the-merchant adjustment (a `debit` line item) — this is a legitimate documented case, not an error; it must be included in Settlement Gap analysis (5.7), not filtered out |
| Settlement delays | Handled via the working-day-aware settlement cycle logic (5.5); a payment not yet appearing in any recon response as of "now" is "not yet settled," not "an error" |
| Different currencies | See Part 9 — do not sum amounts across differing `currency` values |
| Date boundary issues | See Part 6.2 — normalize to a single, explicitly documented timezone (recommended: IST) before bucketing into day/week/month |
| Pagination | `count` max 100 per request (Razorpay Fact, Section 1.1) — any period likely to contain more than 100 records **must** paginate using `skip`, and the calculation layer must not silently process only the first page |
| Large transaction volumes | Aggregation formulas above are defined at the "sum/count over a filtered set" level; the actual implementation (deferred — no code in this document) must not attempt to load an unbounded result set into memory in one call — this is noted as a future implementation constraint, not a metric-definition concern |

---

## Part 9 — Currency

- **Amount unit — [Razorpay Fact]:** All monetary amounts (`payment.amount`, `refund.amount`, `settlement.amount`, fee/tax fields, recon `debit`/`credit`) are expressed in the **smallest currency subunit** — for INR this is **paise** (1 INR = 100 paise), consistently documented across every entity fetched in this research pass (e.g., "if the amount to be charged is ₹299... pass 29900").
- **Currency field — [Razorpay Fact]:** Present on Payment (`currency`), Order (`currency`), Refund (`currency`), and Settlement Recon line items (`currency`) individually — currency is tracked per-transaction, not assumed globally.
- **Settlement currency — [Razorpay Fact]:** Regardless of the payment's original currency, **settlement to the merchant's bank account is always in INR** (Section 1.4/5.5, `razorpay.com/docs/payments/international-payments/faqs/`).
- **INR-only vs. multi-currency support — [Razorpay Fact]:** Razorpay supports 100+ currencies for **accepting** international payments (with international payments specifically enabled on the account), but **settlement is always INR**. For a **standard domestic Indian merchant account** (the primary scope implied by this project's context — "Razorpay AI Finance Controller" for an Indian business), the overwhelming majority of transactions will be `currency: "INR"`.
- **Recommendation for this project [Project Definition]:**
  1. **First implementation should support INR only.** All formulas in Parts 2–7 assume a single currency; do not sum `amount` fields across differing `currency` values.
  2. If any non-INR payment record is encountered (international payments enabled), it must be **excluded from INR-denominated aggregate sums** and reported separately (e.g., a `"nonInrTransactionsExcluded": <count>` field), never silently converted or summed in.
  3. Currency conversion/multi-currency aggregation is explicitly **out of scope** for this document and is a **[Future Enhancement]**, not a Part-2-through-7 formula concern.
- **Status:** `VERIFIED` (subunit and currency-field facts); `VERIFIED WITH ASSUMPTION` (INR-only-for-MVP scoping decision)

---

## Part 10 — Example Data

> **ILLUSTRATIVE EXAMPLE — NOT REAL ACCOUNT DATA**

| Payment | Amount (₹) | Status | Fee (₹) | Tax (₹) | Refund |
|---|---|---|---|---|---|
| A | 1,000 | captured | 20 | 3.6 | — |
| B | 2,000 | captured | 40 | 7.2 | — |
| C | 500 | failed | 0 | 0 | — |
| D | 3,000 | captured | 60 | 10.8 | ₹500 refunded (partial) |

**Calculations (all VERIFIED or VERIFIED WITH ASSUMPTION metrics only):**

- **Transaction Volume** = 4 (A, B, C, D)
- **Successful Transaction Count** = 3 (A, B, D — all `captured`; per 3.6 recommendation, D is included despite its partial refund)
- **Failed Transaction Count** = 1 (C)
- **Success Rate** (denominator = Transaction Volume, per 2.4 recommendation) = 3/4 × 100 = **75%**
- **Failure Rate** = 1/4 × 100 = **25%**
- **Gross Revenue** = 1,000 + 2,000 + 3,000 = **₹6,000** (C excluded, it never captured)
- **Refund Amount** = **₹500** (D's partial refund)
- **Net Revenue (net of refunds)** = 6,000 − 500 = **₹5,500**
- **Razorpay Fee (total)** = 20 + 40 + 60 = **₹120**
- **Razorpay Tax (total)** = 3.6 + 7.2 + 10.8 = **₹21.60**
- **Net Revenue (net of fees)** = 6,000 − 120 − 21.60 = **₹5,858.40**
- **Net Revenue (net of both)** = 6,000 − 500 − 120 − 21.60 = **₹5,358.40**
- **Refund Rate** (definition A, Part 4.3) = 500 / 6,000 × 100 = **8.33%**
- **Refund Count** = 1

---

## Part 11 — Validation Rules

- Percentages (Success Rate, Failure Rate, Refund Rate) must fall within **0–100** under normal circumstances; a value outside this range indicates a calculation bug (e.g., double-counted records), not a valid business state, **except** that Percentage Change (Part 6) is explicitly **not** bounded to 0–100 (it can be negative or exceed 100%).
- All amounts summed together in a single metric **must share the same `currency` value** (Part 9) — mixed-currency summation is a validation failure, not a silent conversion.
- **Division by zero must never occur** — every ratio-based metric (Success Rate, Failure Rate, Refund Rate, Percentage Change) must check its denominator and return an explicit `null`/`undefined` state per Part 6.4, never `0`, never a runtime exception.
- **Duplicate transactions must not be double-counted** — deduplicate by the Razorpay-assigned entity `id` before aggregation (Part 8).
- **Pagination must be fully exhausted** before a period's aggregate is considered final — a metric computed from only the first `count=100` page of a larger result set must not be presented as the complete figure.
- **Date ranges must be explicit and timezone-documented** on every computed metric — no metric may be returned without a `period` object stating its exact start/end and timezone (Part 6.2).
- **Calculations must be deterministic** — given the same underlying Razorpay data and the same period/timezone parameters, the calculation layer must produce bit-for-bit identical output on every run. No LLM, no non-deterministic ordering-dependent logic, and no unseeded randomness may influence any metric in this document.
- **Status field values must match Razorpay's documented enums exactly** (`created`/`authorized`/`captured`/`refunded`/`failed` for payments; `pending`/`processed`/`failed` for refunds) — an unrecognized status value received from the API must be logged as an anomaly, not silently classified into an existing bucket.

---

## Part 12 — Proposed Finance API Output (proposal only — not implemented)

> Fields whose underlying metric is `REQUIRES BUSINESS DECISION` or `NOT VERIFIED` are included here **for structural completeness only** and must not be populated with real values until that metric's status is resolved to `VERIFIED` or `VERIFIED WITH ASSUMPTION` with an approved assumption.

### `GET /finance/payment-performance`

```json
{
  "period": {
    "start": "2026-08-01T00:00:00+05:30",
    "end": "2026-08-23T23:59:59+05:30",
    "timezone": "Asia/Kolkata"
  },
  "currency": "INR",
  "transactionVolume": 0,
  "successfulTransactions": 0,
  "failedTransactions": 0,
  "inProgressTransactions": 0,
  "successRate": {
    "value": null,
    "denominatorBasis": "REQUIRES_BUSINESS_DECISION"
  },
  "failureRate": {
    "value": null,
    "denominatorBasis": "REQUIRES_BUSINESS_DECISION"
  }
}
```

### `GET /finance/revenue`

```json
{
  "period": { "start": "...", "end": "...", "timezone": "Asia/Kolkata" },
  "currency": "INR",
  "grossRevenue": 0,
  "refundAmount": 0,
  "razorpayFee": 0,
  "razorpayTax": 0,
  "netRevenue": {
    "netOfRefunds": 0,
    "netOfFees": 0,
    "netOfRefundsAndFees": 0
  },
  "note": "netRevenue variants are separately labeled per Part 3.4 — no single unqualified 'netRevenue' field is exposed"
}
```

### `GET /finance/refunds`

```json
{
  "period": { "start": "...", "end": "...", "timezone": "Asia/Kolkata" },
  "currency": "INR",
  "refundCount": 0,
  "refundAmount": 0,
  "refundRate": {
    "value": null,
    "definitionUsed": "REQUIRES_BUSINESS_DECISION — candidate A (amount/grossRevenue) recommended"
  }
}
```

### `GET /finance/settlements`

```json
{
  "settlementId": "setl_...",
  "status": "processed",
  "reportedAmount": 0,
  "fees": 0,
  "tax": 0,
  "utr": "...",
  "reconLineItemTotal": {
    "credit": 0,
    "debit": 0
  },
  "settlementGap": {
    "value": 0,
    "withinTolerance": true,
    "toleranceUnit": "paise",
    "note": "Retrospective comparison only — see Part 5.7. No forward-looking 'expectedSettlement' field is exposed."
  }
}
```

### `GET /finance/trends/{metric}`

```json
{
  "metric": "grossRevenue",
  "currentPeriod": { "start": "...", "end": "...", "value": 0 },
  "previousPeriod": { "start": "...", "end": "...", "value": 0 },
  "absoluteChange": 0,
  "percentageChange": null,
  "changeType": "no_activity | new_activity | normal"
}
```

**None of the above fields are finalized for implementation** — this is a proposed shape only, per the task's explicit instruction not to finalize unverified fields.

---

## Part 13 — Test Cases (expected behavior only, no test code)

For **every** VERIFIED / VERIFIED WITH ASSUMPTION metric in Part 7, the following scenarios must be documented and later tested:

1. **Normal case** — a realistic mixed batch of captured, failed, and in-progress payments with some refunds → all metrics compute without error and match hand-calculated expected values (see Part 10's worked example as the reference case).
2. **Empty data** — zero payment records in the period → all counts/sums are 0; all rate metrics return `null` with `changeType: "no_activity"`; no exceptions thrown.
3. **All successful** — every payment `captured`, no failures, no refunds → Success Rate = 100%, Failure Rate = 0%, Refund Rate = 0%.
4. **All failed** — every payment `failed` → Success Rate = 0%, Failure Rate = 100%, Gross Revenue = 0.
5. **Partial refunds** — one or more payments with `refund_status: partial` → confirm Gross Revenue is unaffected, Refund Amount reflects only the partial amount, Net Revenue (net of refunds) is correctly reduced.
6. **Full refunds** — a payment fully refunded → confirm it still counts in Successful Transaction Count and Gross Revenue per 3.6's recommendation, and that Net Revenue nets it fully back out.
7. **Multiple refunds against one payment** — two partial `Refund` records summing to the payment's full amount → confirm Refund Amount sums both individual records correctly and does not double-count or under-count relative to the payment's own `amount_refunded`.
8. **Zero previous-period revenue** — Previous = 0, Current = any value → confirm `percentageChange` is `null` with `changeType: "new_activity"`, not a divide-by-zero exception and not a fabricated `"∞%"` or `"100%"` string.
9. **Settlement mismatch** — recon line items for a `settlement_id` whose credit/debit totals do not match the Settlement entity's reported `amount` beyond tolerance → confirm `settlementGap.withinTolerance = false` is correctly flagged, and the gap value is correctly signed (positive vs. negative).
10. **Different currencies** — a batch containing both `INR` and a non-INR currency payment → confirm the non-INR record is excluded from the INR aggregate sum and separately counted in `nonInrTransactionsExcluded`, never summed together with INR amounts.
11. **Pagination boundary** — a period containing more than 100 payment records (the documented `count` max) → confirm the aggregate reflects **all** records across multiple paginated fetches, not just the first page.
12. **Late authorization re-classification** — a payment initially fetched as `failed`, later re-fetched (within the 3-day late-authorization window) as `authorized`/`captured` → confirm the re-sync process correctly updates prior period counts rather than leaving a stale `failed` classification permanently baked into a previously computed metric.

---

## Implementation Readiness

### SAFE TO IMPLEMENT NOW

Metrics whose definition, data source, and Razorpay semantics are sufficiently verified:

- **Transaction Volume** (2.1)
- **Successful Transaction Count**, using `status == "captured"` (2.2) — assumption about including refunded payments should be confirmed but does not block a first implementation if labeled clearly
- **Failed Transaction Count**, using `status == "failed"` (2.3), **with** a documented caveat about late-authorization re-sync timing
- **Gross Revenue** (3.1)
- **Refund Count** (4.1)
- **Refund Amount**, using framing (a) — attributed to refund creation date (3.3/4.5), pending final sign-off on the attribution choice
- **Razorpay Fee / Tax totals** (3.5)
- **Settlement Amount, Fees, Tax** — direct fields off the Settlement entity (5.2)
- **Settlement Gap (retrospective)** — comparing recon line items to the reported Settlement `amount` (5.7)
- **Percentage Change / zero-denominator handling logic** (6.3/6.4) — the *mechanism* is fully specified and safe to build regardless of which metrics feed into it

### NEEDS BUSINESS DECISION

Metrics where the project must explicitly choose between multiple valid definitions before implementation:

- **Success Rate / Failure Rate denominator** — all attempts vs. decisioned-only attempts (2.4/2.5)
- **"Successful Revenue" as distinct from Gross Revenue** — recommend treating as redundant (3.2)
- **Net Revenue** — which of the three netting variants (or all three) to expose, and under what field name(s) (3.4)
- **Refund Rate formula** — candidate A/B/C (4.3) — recommendation given (A), not yet approved
- **Refunded-payment inclusion in gross figures** — 3.6, recommendation given (include), not yet approved
- **Reporting timezone** — IST recommended (6.2), not yet approved
- **Refund period-attribution** — by refund date vs. original payment date (3.3), recommendation given, not yet approved

### DO NOT IMPLEMENT YET

- **Forward-looking "Expected Settlement" prediction** (5.7) — explicitly flagged as not reliably calculable from available data for the general merchant case (Route/transfers, or any partial-settlement history)
- **`method` field exact enum for INR/India payments** — needs confirmation against a live/sandbox API response before hardcoding a payment-method taxonomy (Section 1.1)
- **Refund `reversed` status** — unclear whether this appears as a literal `status` field value on the Refund entity vs. only as a webhook event name (Section 1.3) — needs confirmation against the Webhooks payload reference
- **Tax-deduction / TDS-specific settlement adjustments** — no official Razorpay documentation was found in this research pass describing a TDS-specific line-item type distinct from the generic `adjustment` type; do not build a `TAX_DEDUCTION` classification that assumes a dedicated, machine-readable Razorpay field for this
- **Multi-currency aggregation / conversion** — explicitly out of scope; INR-only for first implementation (Part 9)

---

## Recommended Implementation Order

Starting with the simplest, most reliably verified metrics and building up to the metrics with the most open questions:

1. **Transaction Volume** — pure count, no status logic needed
2. **Successful Transaction Count** and **Failed Transaction Count** — status-filtered counts, single documented field
3. **Success Rate / Failure Rate** — build the ratio + zero-denominator-handling *mechanism* now, even though the denominator-basis business decision is still open; the mechanism itself doesn't change once the decision is made, only which count feeds it
4. **Gross Revenue** — sum of one documented field, no ambiguity
5. **Razorpay Fee / Tax totals** — same pattern as Gross Revenue, needed as an input to Net Revenue later
6. **Refund Count and Refund Amount** — introduces the refund-attribution decision (3.3) but is otherwise a straightforward sum/count
7. **Net Revenue (all three variants, separately labeled)** — depends on 4 and 6 both being in place; do not collapse into a single field until the business decision (3.4) is resolved
8. **Refund Rate** — depends on 4 and 6; implement with the recommended definition (A) but keep the field explicitly labeled with its definition so it can be swapped without an API-breaking change
9. **Time-based trends (Today/Yesterday, Week, Month)** — depends on the timezone decision (6.2) being resolved first; the percentage-change mechanism itself (6.3/6.4) can and should be unit-built independently ahead of time
10. **Settlement Amount / Fees / Tax (direct fields)** — independent of the above, can be built in parallel once settlement ingestion exists
11. **Settlement Gap (retrospective reconciliation)** — depends on both Settlement entity ingestion and Settlement Recon line-item ingestion being in place; this is the most structurally complex verified metric and should come after the simpler settlement fields
12. **(Deferred) Expected/Predicted Settlement** — do not schedule this until Section 5.7's open questions are resolved with either better documentation or direct confirmation from a live account/sandbox

This order was chosen because each step either has zero open business decisions (steps 1, 2, 4, 5, 10) or clearly isolates its open decision behind a mechanism that can be built and tested independently of that decision being finalized (steps 3, 6, 7, 8, 9), while the genuinely uncertain settlement-prediction metric is pushed to the very end and explicitly may not ship in the first implementation at all.

---

## Self-Review Notes (contradiction / unsupported-assumption check)

Performed before finalizing this document:

- Confirmed no metric is marked `VERIFIED` (full, unqualified) — every revenue/refund/settlement metric that involves a project-level choice is marked `VERIFIED WITH ASSUMPTION` or `REQUIRES BUSINESS DECISION`, consistent with the instruction not to silently choose a definition.
- Confirmed the `Expected Settlement = Payments − Refunds` shortcut is explicitly rejected with reasoning (Section 5.7), per the task's direct instruction not to assume it.
- Confirmed the payment `method` field enum is flagged as unverified for India specifically, rather than reusing the non-INR example response's `card`/`ach` values as if they were the India enum — this was caught and corrected during research (the first-fetched entity page was a non-INR regional variant).
- Confirmed the refund `reversed` status ambiguity (webhook-only vs. entity-field) is flagged rather than silently merged into the three documented statuses.
- Confirmed currency handling never assumes safe cross-currency summation (Part 9), and that settlement-is-always-INR is kept distinct from payment-currency-can-vary.
- Confirmed the zero-denominator rule (Part 6.4) is applied consistently everywhere a ratio is computed (Success Rate, Failure Rate, Refund Rate, Percentage Change) rather than only in the one section where it was first introduced.
- No formula in this document computes a financial number via an LLM; every formula is expressed as a deterministic aggregation over documented fields, consistent with the project's hard requirement that the Finance Intelligence Engine never uses an LLM for calculation.

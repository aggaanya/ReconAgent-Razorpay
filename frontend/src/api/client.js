const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8001'
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000
// Reconciliation explanation makes one LLM call; the chat graph makes two
// sequential LLM calls (plan + interpret). Keep each timeout greater than
// the backend's per-attempt LLM timeout multiplied by the number of calls
// plus HTTP/rendering margin.  Deployments can override at build time.
const AI_RECONCILIATION_TIMEOUT_MS = Number(
  import.meta.env.VITE_AI_RECONCILIATION_TIMEOUT_MS,
) || 150_000
const AI_CHAT_TIMEOUT_MS = Number(
  import.meta.env.VITE_AI_CHAT_TIMEOUT_MS,
) || 270_000

async function request(path, options = {}, { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS } = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(timeoutMs),
    ...options,
  })

  if (!response.ok) {
    throw new Error(`API responded with status ${response.status}`)
  }

  return response.json()
}

/**
 * @typedef {Object} HealthResponse
 * @property {"ok"} status
 */

/**
 * @returns {Promise<HealthResponse>}
 * @throws {Error} on HTTP errors, network failures, or an unexpected payload.
 */
export async function getHealth() {
  const data = await request('/health')
  if (data === null || typeof data !== 'object' || data.status !== 'ok') {
    throw new Error('Unexpected /health response payload')
  }
  return data
}

/**
 * One processed refund line inside ReconciliationEvidence — verbatim
 * from the backend, never recomputed.
 * @typedef {Object} RefundEvidence
 * @property {string} refund_id
 * @property {number} amount_minor
 * @property {string|null} [currency]
 * @property {string|null} [status]
 */

/**
 * Structured audit view of one reconciliation decision. Every value is
 * copied from the deterministic engine output (records, component
 * amounts, expected vs actual) — presentation code must render it
 * as-is and never derive new financial values from it.
 * @typedef {Object} ReconciliationEvidence
 * @property {string|null} payment_id
 * @property {number|null} payment_amount_minor
 * @property {string|null} settlement_id
 * @property {number|null} settlement_amount_minor
 * @property {string[]} additional_settlement_ids - extra line ids beyond
 *   the primary (DUPLICATE_SETTLEMENT); empty otherwise
 * @property {RefundEvidence[]} refunds - itemized processed refunds
 * @property {number|null} gross_amount_minor
 * @property {number|null} refunded_total_minor
 * @property {number|null} fee_minor
 * @property {number|null} tax_minor
 * @property {number|null} expected_settlement_minor
 * @property {number|null} actual_settlement_minor
 * @property {number|null} difference_minor
 * @property {string} status - terminal reconciliation status
 * @property {string[]} rules_triggered - terminal status value first,
 *   then any secondary issue values (e.g. SETTLEMENT_DELAY)
 * @property {string} reason - the engine's deterministic reason, verbatim
 */

/**
 * @typedef {Object} ReconciliationException
 * @property {string} source_transaction_id
 * @property {string|null} matched_transaction_id
 * @property {string} status
 * @property {string[]} [secondary_issues] - verbatim from the engine:
 *   additional detected issues that did not win primary classification
 * @property {number|null} expected_amount_minor
 * @property {number|null} actual_amount_minor
 * @property {number|null} difference_minor
 * @property {string|null} currency
 * @property {string} reason
 * @property {string} exception_type
 * @property {"INFO"|"LOW"|"MEDIUM"|"HIGH"|"CRITICAL"} [severity] -
 *   deterministic triage severity from the backend policy layer
 * @property {number} [priority] - documented bounded triage rank
 *   (CRITICAL=100 … INFO=10); deterministic, not a learned score
 * @property {string} [recommended_action] - deterministic operator
 *   guidance for exceptions; never present on matched records
 * @property {ReconciliationEvidence} [evidence] - structured audit view
 *   attached by the backend; null when produced without the evidence pass
 */

/**
 * @typedef {Object} ReconciliationResponse
 * @property {"completed"|"partial"|"failed"} status
 * @property {string|null} answer - LLM narrative; facts are authoritative.
 * @property {number} total_records
 * @property {number} matched_count
 * @property {number} exception_count
 * @property {number} unresolved_count
 * @property {number|null} match_rate
 * @property {number|null} accuracy - always null in serving by design;
 *   measured accuracy lives in scripts/reconcile_benchmark.py.
 * @property {number} processing_time_ms
 * @property {number} throughput_records_per_second
 * @property {Record<string, number>} exception_breakdown
 * @property {ReconciliationException[]} exceptions
 * @property {string[]} errors
 */

/**
 * Runs the deterministic Track 04 reconciliation over a seeded batch.
 *
 * @param {{source?: string, seed?: number, size?: number, explain?: boolean}} [params]
 * @returns {Promise<ReconciliationResponse>}
 */
export async function postAiReconcile(params = {}) {
  return request(
    '/api/v1/ai/reconcile',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: 'synthetic',
        seed: 42,
        size: 100,
        explain: true,
        ...params,
      }),
    },
    { timeoutMs: AI_RECONCILIATION_TIMEOUT_MS },
  )
}

/**
 * @typedef {Object} EvaluationDataset
 * @property {string} source
 * @property {number} seed
 * @property {number} size
 */

/**
 * @typedef {Object} EvaluationResponse
 * @property {"evaluation"} surface - marks this as evaluation-only output
 * @property {EvaluationDataset} dataset
 * @property {number} total_records
 * @property {number} matched_count
 * @property {number} exception_count
 * @property {number} unresolved_count
 * @property {number|null} match_rate
 * @property {number|null} accuracy - measured vs ground truth (evaluation
 *   surface only; the serving endpoint keeps accuracy null by design)
 * @property {number|null} precision
 * @property {number|null} recall
 * @property {number|null} f1
 * @property {number} true_positives
 * @property {number} false_positives
 * @property {number} false_negatives
 * @property {number} throughput_records_per_second
 * @property {Record<string, number>} exception_breakdown
 * @property {{case_id: string, kind: string, expected: string, actual: string}[]} mismatches
 */

/**
 * Fetches the evaluation-only report for the seeded batch: the same run
 * measured against the isolated ground-truth evaluator. No credentials,
 * no database, no LLM.
 *
 * @param {{seed?: number, size?: number}} [params]
 * @returns {Promise<EvaluationResponse>}
 */
export async function getReconcileEvaluation(params = {}) {
  const query = new URLSearchParams({
    seed: String(params.seed ?? 42),
    size: String(params.size ?? 100),
  })
  return request(`/api/v1/ai/reconcile/evaluation?${query.toString()}`)
}

/**
 * @typedef {Object} AiChatResponse
 * @property {string} question - echoed verbatim (trimmed)
 * @property {"completed"|"partial"|"failed"} status
 *   completed: data retrieved and interpreted; partial: data retrieved but
 *   some tools or the interpretation failed; failed: nothing retrieved.
 * @property {string|null} answer - LLM narrative over the retrieved facts;
 *   facts in tool_results/financial_signals are authoritative.
 * @property {string[]} selected_tools
 * @property {string} selection_source
 * @property {Record<string, Object>} tool_results - verbatim Finance Tool
 *   envelopes: deterministic facts only
 * @property {Record<string, string>} tool_errors
 * @property {Object[]} financial_signals - deterministic Signal Analysis
 *   Engine findings
 * @property {string[]} errors
 */

/**
 * Asks the finance intelligence agent one natural-language question via
 * the existing LangGraph chat endpoint. Needs a configured LLM key on the
 * backend; without one the server responds 503 and this rejects with an
 * Error whose message names the status.
 *
 * @param {string} question - non-empty, max 1000 characters (server-enforced)
 * @returns {Promise<AiChatResponse>}
 */
export async function postAiChat(question) {
  return request(
    '/api/v1/ai/chat',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    },
    { timeoutMs: AI_CHAT_TIMEOUT_MS },
  )
}

/**
 * Runs a deterministic What-Changed comparison between two reconciliation runs.
 *
 * @param {{previous_seed?: number, previous_size?: number, current_seed?: number, current_size?: number, explain?: boolean}} [params]
 * @returns {Promise<Object>}
 */
export async function postReconcileCompare(params = {}) {
  return request('/api/v1/ai/reconcile/compare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      previous_seed: 41,
      previous_size: 100,
      current_seed: 42,
      current_size: 100,
      explain: true,
      ...params,
    }),
  })
}

export function getApiBaseUrl() {
  return API_BASE_URL
}

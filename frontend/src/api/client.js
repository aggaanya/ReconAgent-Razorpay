const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(10000),
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
  return request('/api/v1/ai/reconcile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: 'synthetic',
      seed: 42,
      size: 100,
      explain: true,
      ...params,
    }),
  })
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

export function getApiBaseUrl() {
  return API_BASE_URL
}

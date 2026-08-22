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

export function getApiBaseUrl() {
  return API_BASE_URL
}

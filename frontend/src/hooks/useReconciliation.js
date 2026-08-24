import { useCallback, useEffect, useState } from 'react'
import { postAiReconcile } from '../api/client.js'

export const RECONCILE_STATUS = {
  IDLE: 'idle',
  RUNNING: 'running',
  READY: 'ready',
  UNAVAILABLE: 'unavailable',
}

/**
 * Runs the deterministic reconciliation report on demand.
 * All numbers come from the backend engine — nothing is computed here.
 */
export function useReconciliation(params = {}) {
  const [status, setStatus] = useState(RECONCILE_STATUS.IDLE)
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)

  const run = useCallback(async () => {
    setStatus(RECONCILE_STATUS.RUNNING)
    setError(null)
    try {
      const data = await postAiReconcile(params)
      setReport(data)
      setStatus(RECONCILE_STATUS.READY)
    } catch (err) {
      setError(
        err instanceof Error
          ? `${err.message} — could not reach the reconciliation endpoint.`
          : 'Unknown error while reconciling.',
      )
      setStatus(RECONCILE_STATUS.UNAVAILABLE)
    }
  }, [params.source, params.seed, params.size, params.explain])

  useEffect(() => {
    run()
  }, [run])

  return { status, report, error, rerun: run }
}

import { useCallback, useEffect, useState } from 'react'
import { getReconcileEvaluation } from '../api/client.js'

export const EVALUATION_STATUS = {
  IDLE: 'idle',
  RUNNING: 'running',
  READY: 'ready',
  UNAVAILABLE: 'unavailable',
}

/**
 * Fetches the evaluation-only measured-quality report (accuracy,
 * precision/recall/F1, FP/FN) for the seeded batch. The backend computes
 * everything — this hook only transports state.
 */
export function useReconcileEvaluation(params = {}) {
  const [status, setStatus] = useState(EVALUATION_STATUS.IDLE)
  const [evaluation, setEvaluation] = useState(null)
  const [error, setError] = useState(null)

  const run = useCallback(async () => {
    setStatus(EVALUATION_STATUS.RUNNING)
    setError(null)
    try {
      const data = await getReconcileEvaluation(params)
      if (!data || data.surface !== 'evaluation') {
        throw new Error('Unexpected evaluation response payload')
      }
      setEvaluation(data)
      setStatus(EVALUATION_STATUS.READY)
    } catch (err) {
      setError(
        err instanceof Error
          ? `${err.message} — could not reach the evaluation endpoint.`
          : 'Unknown error while evaluating.',
      )
      setStatus(EVALUATION_STATUS.UNAVAILABLE)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.seed, params.size])

  useEffect(() => {
    run()
  }, [run])

  return { status, evaluation, error, rerun: run }
}

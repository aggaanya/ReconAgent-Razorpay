import { useCallback, useEffect, useState } from 'react'
import { getHealth } from '../api/client.js'

export const BACKEND_STATUS = {
  CHECKING: 'checking',
  CONNECTED: 'connected',
  UNAVAILABLE: 'unavailable',
}

export function useBackendHealth() {
  const [status, setStatus] = useState(BACKEND_STATUS.CHECKING)
  const [error, setError] = useState(null)

  const checkHealth = useCallback(async () => {
    setStatus(BACKEND_STATUS.CHECKING)
    setError(null)
    try {
      await getHealth()
      setStatus(BACKEND_STATUS.CONNECTED)
    } catch (err) {
      setStatus(BACKEND_STATUS.UNAVAILABLE)
      setError(
        err instanceof Error
          ? `${err.message} — could not reach the backend health endpoint.`
          : 'Unknown error while contacting the backend.',
      )
    }
  }, [])

  useEffect(() => {
    checkHealth()
  }, [checkHealth])

  return { status, error, refresh: checkHealth }
}

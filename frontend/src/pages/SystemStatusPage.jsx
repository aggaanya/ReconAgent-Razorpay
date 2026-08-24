import { useBackendHealth, BACKEND_STATUS } from '../hooks/useBackendHealth.js'
import { getApiBaseUrl } from '../api/client.js'
import StatusPill from '../components/StatusPill.jsx'

const LABELS = {
  [BACKEND_STATUS.CONNECTED]: 'Connected',
  [BACKEND_STATUS.UNAVAILABLE]: 'Unavailable',
  [BACKEND_STATUS.CHECKING]: 'Checking backend...',
}

export default function SystemStatusPage() {
  const { status, error, refresh } = useBackendHealth()

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
      <section className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">ReconAgent</h1>
        <p className="mt-1 text-sm text-slate-500">Frontend / FastAPI connectivity check</p>

        <div className="mt-6 rounded-lg border border-slate-200 p-4">
          <p className="text-sm font-medium text-slate-700">Backend Status:</p>
          <div className="mt-3">
            {status === BACKEND_STATUS.CHECKING ? (
              <p className="text-sm text-slate-500" role="status">
                Checking backend...
              </p>
            ) : (
              <StatusPill status={status} label={LABELS[status]} />
            )}
          </div>

          {status === BACKEND_STATUS.UNAVAILABLE && (
            <div className="mt-3 rounded-md bg-red-50 p-3" role="alert">
              <p className="text-xs text-red-600">{error || `Could not reach ${getApiBaseUrl()}/health`}</p>
            </div>
          )}

          <p className="mt-3 text-xs text-slate-400">
            Razorpay API integration is optional &mdash; reconciliation runs on
            the synthetic demo dataset without any credentials.
          </p>
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-slate-400">
          <span>API: {getApiBaseUrl()}</span>
          <button
            type="button"
            onClick={refresh}
            disabled={status === BACKEND_STATUS.CHECKING}
            className="rounded-md border border-slate-300 px-3 py-1.5 font-medium text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Retry
          </button>
        </div>
      </section>
    </main>
  )
}

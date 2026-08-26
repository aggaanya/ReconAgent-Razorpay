import { useBackendHealth, BACKEND_STATUS } from '../hooks/useBackendHealth.js'
import { getApiBaseUrl } from '../api/client.js'
import StatusPill from '../components/StatusPill.jsx'

const LABELS = {
  [BACKEND_STATUS.CONNECTED]: 'Connected',
  [BACKEND_STATUS.UNAVAILABLE]: 'Unavailable',
  [BACKEND_STATUS.CHECKING]: 'Checking...',
}

export default function SettingsPage() {
  const { status, error, refresh } = useBackendHealth()

  return (
    <div data-testid="settings-page">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Settings</h1>
        <p className="mt-1 text-sm text-slate-500">
          System configuration and backend connectivity
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">Backend Connection</h2>
          <dl className="mt-4 space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <dt className="text-slate-500">Status</dt>
              <dd>
                {status === BACKEND_STATUS.CHECKING ? (
                  <span className="text-sm text-slate-500">Checking...</span>
                ) : (
                  <StatusPill status={status} label={LABELS[status]} />
                )}
              </dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-slate-500">API Base URL</dt>
              <dd className="font-mono text-xs text-slate-700">{getApiBaseUrl()}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-slate-500">Health Endpoint</dt>
              <dd className="font-mono text-xs text-slate-700">{getApiBaseUrl()}/health</dd>
            </div>
          </dl>

          {error && (
            <div className="mt-4 rounded-lg bg-red-50 p-3 text-xs text-red-700" role="alert">
              {error}
            </div>
          )}

          <button
            type="button"
            onClick={refresh}
            disabled={status === BACKEND_STATUS.CHECKING}
            className="mt-4 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Test Connection
          </button>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">API Endpoints</h2>
          <dl className="mt-4 space-y-3 text-sm">
            <div className="flex items-start justify-between gap-4">
              <dt className="shrink-0 text-slate-500">Reconciliation</dt>
              <dd className="font-mono text-xs text-slate-700">POST /api/v1/ai/reconcile</dd>
            </div>
            <div className="flex items-start justify-between gap-4">
              <dt className="shrink-0 text-slate-500">Evaluation</dt>
              <dd className="font-mono text-xs text-slate-700">GET /api/v1/ai/reconcile/evaluation</dd>
            </div>
            <div className="flex items-start justify-between gap-4">
              <dt className="shrink-0 text-slate-500">AI Chat</dt>
              <dd className="font-mono text-xs text-slate-700">POST /api/v1/ai/chat</dd>
            </div>
            <div className="flex items-start justify-between gap-4">
              <dt className="shrink-0 text-slate-500">Drift Comparison</dt>
              <dd className="font-mono text-xs text-slate-700">POST /api/v1/ai/reconcile/compare</dd>
            </div>
            <div className="flex items-start justify-between gap-4">
              <dt className="shrink-0 text-slate-500">Health</dt>
              <dd className="font-mono text-xs text-slate-700">GET /health</dd>
            </div>
          </dl>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">About</h2>
          <dl className="mt-4 space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">Application</dt>
              <dd className="font-medium text-slate-900">ReconAgent</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Version</dt>
              <dd className="text-slate-700">0.1.0</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Frontend</dt>
              <dd className="text-slate-700">React 19 + Vite + Tailwind CSS</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Backend</dt>
              <dd className="text-slate-700">FastAPI + LangGraph + Ollama</dd>
            </div>
          </dl>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">Configuration</h2>
          <p className="mt-1 text-xs text-slate-500">
            Environment variables are configured on the backend.
          </p>
          <dl className="mt-4 space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">VITE_API_BASE_URL</dt>
              <dd className="font-mono text-xs text-slate-700">
                {import.meta.env.VITE_API_BASE_URL || '(not set)'}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Default Source</dt>
              <dd className="text-slate-700">synthetic</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Default Seed</dt>
              <dd className="tabular-nums text-slate-700">42</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Default Size</dt>
              <dd className="tabular-nums text-slate-700">100</dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  )
}

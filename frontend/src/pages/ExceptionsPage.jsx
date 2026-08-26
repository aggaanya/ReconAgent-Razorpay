import { useReconciliation, RECONCILE_STATUS } from '../hooks/useReconciliation.js'
import ExceptionTable from '../components/ExceptionTable.jsx'

export default function ExceptionsPage() {
  const { status, report, error } = useReconciliation({
    source: 'synthetic',
    seed: 42,
    size: 100,
    explain: false,
  })

  const loading = status === RECONCILE_STATUS.RUNNING

  return (
    <div data-testid="exceptions-page">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Exceptions</h1>
        <p className="mt-1 text-sm text-slate-500">
          Typed reconciliation exceptions from the deterministic engine.
          Every row carries its stated reason and severity.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert">
          {error}
        </div>
      )}

      {loading && !report && (
        <div className="flex items-center justify-center py-20">
          <div className="text-sm text-slate-500">Loading exceptions...</div>
        </div>
      )}

      {report && (
        <>
          <div className="mb-4 flex items-center gap-4 text-sm text-slate-500">
            <span>
              <span className="font-semibold text-slate-900">{report.exceptions.length}</span> exceptions
            </span>
            <span className="text-slate-300">|</span>
            <span>
              <span className="font-semibold text-slate-900">{report.total_records}</span> total records
            </span>
            <span className="text-slate-300">|</span>
            <span>
              <span className="font-semibold text-slate-900">{report.matched_count}</span> matched
            </span>
          </div>

          <ExceptionTable exceptions={report.exceptions} />
        </>
      )}
    </div>
  )
}

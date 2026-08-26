import { useReconcileEvaluation, EVALUATION_STATUS } from '../hooks/useReconcileEvaluation.js'

function pct(value) {
  return value === null || value === undefined ? '—' : `${Number(value).toFixed(2)}%`
}

function num(value) {
  return value === null || value === undefined ? '—' : Math.round(value).toLocaleString('en-IN')
}

const METRIC_CARDS = [
  { key: 'accuracy', label: 'Accuracy', description: 'Correct classifications / total records' },
  { key: 'precision', label: 'Precision', description: 'True positives / (true positives + false positives)' },
  { key: 'recall', label: 'Recall', description: 'True positives / (true positives + false negatives)' },
  { key: 'f1', label: 'F1 Score', description: 'Harmonic mean of precision and recall' },
]

const COUNTER_CARDS = [
  { key: 'true_positives', label: 'True Positives', color: 'text-emerald-700' },
  { key: 'false_positives', label: 'False Positives', color: 'text-red-600' },
  { key: 'false_negatives', label: 'False Negatives', color: 'text-amber-600' },
]

export default function EvaluationPage() {
  const { status, evaluation, error, rerun } = useReconcileEvaluation({ seed: 42, size: 100 })
  const loading = status === EVALUATION_STATUS.RUNNING

  return (
    <div data-testid="evaluation-page">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Evaluation / Benchmark</h1>
        <p className="mt-1 text-sm text-slate-500">
          Ground-truth evaluation metrics for the seeded reconciliation batch.
          These are measured by the isolated evaluator &mdash; they are <strong>not</strong> runtime serving metrics.
        </p>
      </header>

      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
        <p className="font-semibold">Important: Evaluation vs Serving</p>
        <p className="mt-1">
          These metrics measure accuracy against isolated ground truth. The normal reconciliation
          API intentionally returns <code className="rounded bg-amber-100 px-1">accuracy: null</code> &mdash;
          ground truth never enters the serving pipeline.
        </p>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert">
          {error}
          <button
            type="button"
            onClick={rerun}
            className="mt-2 rounded-md bg-white px-3 py-1.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
          >
            Retry
          </button>
        </div>
      )}

      {loading && !evaluation && (
        <div className="flex items-center justify-center py-20">
          <div className="text-sm text-slate-500">Loading evaluation metrics...</div>
        </div>
      )}

      {evaluation && (
        <>
          <div className="mt-6 flex items-center gap-3">
            <span className="inline-flex items-center rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
              Evaluation surface &mdash; ground truth benchmark
            </span>
            <span className="text-xs text-slate-400">
              Seed {evaluation.dataset.seed} &middot; {evaluation.dataset.size} records
            </span>
          </div>

          <section className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
            {METRIC_CARDS.map(({ key, label, description }) => (
              <div key={key} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <p className="text-sm font-medium text-slate-500">{label}</p>
                <p className="mt-2 text-3xl font-bold tabular-nums text-emerald-700">
                  {pct(evaluation[key])}
                </p>
                <p className="mt-1 text-xs text-slate-400">{description}</p>
              </div>
            ))}
          </section>

          <section className="mt-6 grid grid-cols-3 gap-4">
            {COUNTER_CARDS.map(({ key, label, color }) => (
              <div key={key} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <p className="text-sm font-medium text-slate-500">{label}</p>
                <p className={`mt-2 text-3xl font-bold tabular-nums ${color}`}>
                  {num(evaluation[key])}
                </p>
              </div>
            ))}
          </section>

          <section className="mt-6 grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-medium text-slate-500">Run Summary</h3>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-slate-500">Total Records</dt>
                  <dd className="font-semibold tabular-nums">{num(evaluation.total_records)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Matched</dt>
                  <dd className="font-semibold tabular-nums text-emerald-700">{num(evaluation.matched_count)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Exceptions</dt>
                  <dd className="font-semibold tabular-nums text-red-600">{num(evaluation.exception_count)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Unresolved</dt>
                  <dd className="font-semibold tabular-nums">{num(evaluation.unresolved_count)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Match Rate</dt>
                  <dd className="tabular-nums">{pct(evaluation.match_rate)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Throughput</dt>
                  <dd className="tabular-nums">{num(evaluation.throughput_records_per_second)} rec/s</dd>
                </div>
              </dl>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-medium text-slate-500">Exception Breakdown</h3>
              <div className="mt-3 space-y-2">
                {Object.entries(evaluation.exception_breakdown)
                  .sort((a, b) => b[1] - a[1])
                  .map(([type, count]) => (
                    <div key={type} className="flex items-center justify-between text-sm">
                      <span className="font-mono text-xs text-slate-600">{type}</span>
                      <span className="font-semibold tabular-nums text-slate-900">{count}</span>
                    </div>
                  ))}
                {Object.keys(evaluation.exception_breakdown).length === 0 && (
                  <p className="text-sm text-slate-400">No exceptions</p>
                )}
              </div>
            </div>
          </section>

          {evaluation.mismatches.length > 0 && (
            <section className="mt-6 rounded-xl border border-red-200 bg-red-50 p-5">
              <h3 className="text-sm font-semibold text-red-800">
                {evaluation.mismatches.length} Deviation(s) vs Ground Truth
              </h3>
              <ul className="mt-2 space-y-1 text-xs text-red-700">
                {evaluation.mismatches.map((m, i) => (
                  <li key={i}>
                    Case {m.case_id}: {m.kind} &mdash; expected {m.expected}, got {m.actual}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="mt-6 flex items-center gap-3">
            <button
              type="button"
              onClick={rerun}
              disabled={loading}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Measuring...' : 'Re-run Evaluation'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

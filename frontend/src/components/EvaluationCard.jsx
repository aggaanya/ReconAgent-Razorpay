import { EVALUATION_STATUS } from '../hooks/useReconcileEvaluation.js'

function pct(value) {
  return value === null || value === undefined
    ? '—'
    : `${Number(value).toFixed(2)}%`
}

function num(value) {
  return value === null || value === undefined
    ? '—'
    : Math.round(value).toLocaleString('en-IN')
}

const METRICS = [
  { key: 'records', label: 'Records', get: (e) => num(e.total_records) },
  { key: 'matchRate', label: 'Match Rate', get: (e) => pct(e.match_rate) },
  { key: 'accuracy', label: 'Accuracy', get: (e) => pct(e.accuracy), accent: true },
  { key: 'precision', label: 'Precision', get: (e) => pct(e.precision), accent: true },
  { key: 'recall', label: 'Recall', get: (e) => pct(e.recall), accent: true },
  { key: 'f1', label: 'F1', get: (e) => pct(e.f1), accent: true },
  { key: 'truePositives', label: 'True Positives', get: (e) => num(e.true_positives), accent: true },
  { key: 'falsePositives', label: 'False Positives', get: (e) => num(e.false_positives) },
  { key: 'falseNegatives', label: 'False Negatives', get: (e) => num(e.false_negatives) },
  {
    key: 'throughput',
    label: 'Throughput',
    get: (e) => `${num(e.throughput_records_per_second)} rec/s`,
  },
]

/**
 * Evaluation-only measured quality for the same seeded batch:
 * accuracy/precision/recall/F1 and FP/FN come from the backend's
 * isolated ground-truth evaluator — never computed in the browser.
 */
export default function EvaluationCard({ status, evaluation, error, onRetry }) {
  if (status === EVALUATION_STATUS.UNAVAILABLE) {
    return (
      <section
        className="mt-8 rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-800"
        data-testid="evaluation-section"
      >
        <h2 className="text-lg font-semibold text-slate-900">
          Reconciliation Evaluation
        </h2>
        <p className="mt-2" role="alert">
          {error || 'Evaluation unavailable.'}
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg bg-white px-3 py-2 text-sm font-medium text-slate-600 ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
        >
          Retry evaluation
        </button>
      </section>
    )
  }

  const perfect =
    evaluation !== null &&
    evaluation.accuracy === 100 &&
    evaluation.false_positives === 0 &&
    evaluation.false_negatives === 0 &&
    evaluation.mismatches.length === 0

  return (
    <section
      className="mt-8 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      data-testid="evaluation-section"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-900">
          Reconciliation Evaluation
        </h2>
        <span
          className={`rounded-full px-3 py-1 text-xs font-medium ${
            perfect
              ? 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200'
              : 'bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200'
          }`}
          data-testid="evaluation-verdict"
        >
          {status === EVALUATION_STATUS.RUNNING
            ? 'Measuring…'
            : perfect
              ? 'Measured vs ground truth · PASS'
              : 'Deviations detected'}
        </span>
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Measured by the isolated ground-truth evaluator — evaluation surface
        only. The serving API never sees ground truth.
      </p>

      {evaluation ? (
        <>
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
            {METRICS.map((metric) => (
              <div
                key={metric.key}
                className="rounded-lg border border-slate-100 bg-slate-50 p-3"
                data-testid={`evaluation-${metric.key}`}
              >
                <dt className="text-xs font-medium text-slate-500">
                  {metric.label}
                </dt>
                <dd
                  className={`mt-1 text-lg font-semibold tabular-nums ${
                    metric.accent ? 'text-emerald-700' : 'text-slate-900'
                  }`}
                >
                  {metric.get(evaluation)}
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-4 text-sm text-slate-600" data-testid="evaluation-honesty">
            Honest exception list:{' '}
            <span className="font-semibold">{evaluation.exception_count}</span>{' '}
            exceptions reported, including{' '}
            <span className="font-semibold">{evaluation.unresolved_count}</span>{' '}
            unresolved case{evaluation.unresolved_count === 1 ? '' : 's'} the
            engine refuses to guess about.
          </p>

          {evaluation.mismatches.length > 0 ? (
            <div
              className="mt-3 rounded-lg bg-red-50 p-3 text-xs text-red-700"
              role="alert"
              data-testid="evaluation-mismatches"
            >
              {evaluation.mismatches.length} deviation(s) vs ground truth:
              <ul className="mt-1 list-disc pl-5">
                {evaluation.mismatches.map((m) => (
                  <li key={`${m.case_id}-${m.kind}`}>
                    case={m.case_id} kind={m.kind} expected={m.expected}{' '}
                    actual={m.actual}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : (
        <p className="mt-4 text-sm text-slate-500" role="status">
          Measuring…
        </p>
      )}
    </section>
  )
}

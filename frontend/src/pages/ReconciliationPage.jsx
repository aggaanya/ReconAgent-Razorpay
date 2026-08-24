import { useState } from 'react'
import { getApiBaseUrl } from '../api/client.js'
import EvaluationCard from '../components/EvaluationCard.jsx'
import ExceptionTable from '../components/ExceptionTable.jsx'
import MatchBreakdownChart from '../components/MatchBreakdownChart.jsx'
import SummaryCard from '../components/SummaryCard.jsx'
import {
  RECONCILE_STATUS,
  useReconciliation,
} from '../hooks/useReconciliation.js'
import { useReconcileEvaluation } from '../hooks/useReconcileEvaluation.js'

function formatPercent(value) {
  return value === null || value === undefined ? '—' : `${value.toFixed(2)}%`
}

export default function ReconciliationPage() {
  // Canonical Track 04 batch. Facts load with zero configuration
  // (explain=false); the LLM narrative is an explicit opt-in.
  const [explain, setExplain] = useState(false)
  const { status, report, error, rerun } = useReconciliation({
    source: 'synthetic',
    seed: 42,
    size: 100,
    explain,
  })
  // Same batch, measured against the isolated ground-truth evaluator
  // (evaluation-only surface; serving accuracy stays null by design).
  const evaluation = useReconcileEvaluation({ seed: 42, size: 100 })

  const loading = status === RECONCILE_STATUS.RUNNING
  const summary = report
    ? [
        {
          label: 'Total Records',
          value: report.total_records,
          accent: 'slate',
        },
        { label: 'Matched', value: report.matched_count, accent: 'emerald' },
        { label: 'Exceptions', value: report.exception_count, accent: 'red' },
        {
          label: 'Match Rate',
          value: formatPercent(report.match_rate),
          accent: 'amber',
        },
        {
          label: 'Accuracy',
          value:
            evaluation.evaluation === null
              ? '—'
              : formatPercent(evaluation.evaluation?.accuracy),
          accent: 'emerald',
          hint: 'Live below — measured vs isolated ground truth',
        },
      ]
    : []

  return (
    <main className="mx-auto max-w-5xl px-6 py-10" data-testid="reconciliation-page">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">
            Batch Reconciliation
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Source:{' '}
            <span className="font-medium text-slate-700">
              Synthetic Demo Dataset
            </span>{' '}
            (seeded, deterministic &middot; no credentials required) &middot;{' '}
            Razorpay API integration is optional &middot; backend{' '}
            <code className="rounded bg-slate-100 px-1">{getApiBaseUrl()}</code>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setExplain((v) => !v)}
            aria-pressed={explain}
            className={`rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset ${
              explain
                ? 'bg-indigo-600 text-white ring-indigo-600 hover:bg-indigo-500'
                : 'bg-white text-slate-600 ring-slate-300 hover:bg-slate-50'
            }`}
            data-testid="explain-toggle"
          >
            AI explanation: {explain ? 'On' : 'Off'}
          </button>
          <button
            type="button"
            onClick={rerun}
            disabled={loading}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
            data-testid="reconcile-run"
          >
            {loading ? 'Reconciling…' : 'Run reconciliation'}
          </button>
        </div>
      </header>

      {error ? (
        <p
          className="mt-6 rounded-lg bg-red-50 p-4 text-sm text-red-700 ring-1 ring-inset ring-red-600/20"
          data-testid="reconcile-error"
        >
          {error}
        </p>
      ) : null}

      {report ? (
        <>
          <section className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            {summary.map((card) => (
              <SummaryCard key={card.label} {...card} />
            ))}
          </section>

          {/* Deterministic facts first — the LLM narrative lives below. */}
          <section className="mt-6 grid gap-4 lg:grid-cols-2">
            <MatchBreakdownChart
              matchedCount={report.matched_count}
              exceptionCount={report.exception_count}
            />
            <div
              className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
              data-testid="run-metadata"
            >
              <h3 className="text-sm font-medium text-slate-500">Run details</h3>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-slate-500">Status</dt>
                  <dd className="font-medium">{report.status}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-slate-500">Unresolved (human review)</dt>
                  <dd className="font-medium tabular-nums">
                    {report.unresolved_count}
                  </dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-slate-500">Processing time</dt>
                  <dd className="tabular-nums">
                    {report.processing_time_ms.toFixed(3)} ms
                  </dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-slate-500">Throughput</dt>
                  <dd className="tabular-nums">
                    {Math.round(report.throughput_records_per_second).toLocaleString(
                      'en-IN',
                    )}{' '}
                    rec/s
                  </dd>
                </div>
              </dl>
            </div>
          </section>

          <EvaluationCard
            status={evaluation.status}
            evaluation={evaluation.evaluation}
            error={evaluation.error}
            onRetry={evaluation.rerun}
          />

          <section className="mt-8" data-testid="exceptions-section">
            <h2 className="text-lg font-semibold text-slate-900">
              Exceptions ({report.exceptions.length})
            </h2>
            <p className="mb-4 mt-1 text-sm text-slate-500">
              Typed by the engine — every row carries its stated reason.
            </p>
            <ExceptionTable exceptions={report.exceptions} />
          </section>

          {/* AI explanation is deliberately separated from the numbers. */}
          <section className="mt-8" data-testid="ai-explanation-section">
            <h2 className="text-lg font-semibold text-slate-900">
              AI explanation
            </h2>
            <p className="mb-4 mt-1 text-sm text-slate-500">
              Narrative only — the figures above remain the source of truth.
            </p>
            {report.answer ? (
              <div
                className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-5 text-sm leading-relaxed text-slate-700 whitespace-pre-line"
                data-testid="ai-explanation"
              >
                {report.answer}
              </div>
            ) : (
              <p
                className="rounded-xl border border-dashed border-slate-300 p-5 text-sm text-slate-500"
                data-testid="ai-explanation-missing"
              >
                No narrative available
                {report.errors.length ? ` — ${report.errors.join(' ')}` : ''}.{' '}
                {explain
                  ? ''
                  : "Use the 'AI explanation' toggle to request one. "}The
                deterministic report above is unaffected.
              </p>
            )}
          </section>
        </>
      ) : null}
    </main>
  )
}

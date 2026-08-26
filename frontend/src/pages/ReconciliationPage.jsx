import { useState } from 'react'
import SummaryCard from '../components/SummaryCard.jsx'
import MatchBreakdownChart from '../components/MatchBreakdownChart.jsx'
import PriorityBreakdown from '../components/PriorityBreakdown.jsx'
import WhatChangedPanel from '../components/WhatChangedPanel.jsx'
import { postAiReconcile } from '../api/client.js'

const SOURCES = ['synthetic']

function formatPercent(value) {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)}%`
}

export default function ReconciliationPage() {
  const [source, setSource] = useState('synthetic')
  const [seed, setSeed] = useState(42)
  const [size, setSize] = useState(100)
  const [explain, setExplain] = useState(true)
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleRun() {
    setLoading(true)
    setError(null)
    try {
      const data = await postAiReconcile({ source, seed, size, explain })
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reconciliation failed.')
      setReport(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div data-testid="reconciliation-page">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Reconciliation</h1>
        <p className="mt-1 text-sm text-slate-500">
          Run deterministic reconciliation over a seeded batch with optional AI explanation
        </p>
      </header>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">Configuration</h2>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label htmlFor="source-select" className="mb-1 block text-xs font-medium text-slate-500">
              Source
            </label>
            <select
              id="source-select"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
            >
              {SOURCES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="seed-input" className="mb-1 block text-xs font-medium text-slate-500">
              Seed
            </label>
            <input
              id="seed-input"
              type="number"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
            />
          </div>

          <div>
            <label htmlFor="size-input" className="mb-1 block text-xs font-medium text-slate-500">
              Dataset Size
            </label>
            <input
              id="size-input"
              type="number"
              value={size}
              onChange={(e) => setSize(Number(e.target.value))}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
            />
          </div>

          <div className="flex items-end">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={explain}
                onChange={(e) => setExplain(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-500"
              />
              <span className="text-sm font-medium text-slate-700">AI Explanation</span>
            </label>
          </div>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={handleRun}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
            data-testid="reconcile-run"
          >
            {loading ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                Reconciling...
              </>
            ) : (
              'Run Reconciliation'
            )}
          </button>
          {report && (
            <span className="text-xs text-slate-400">
              Last run: {report.processing_time_ms.toFixed(1)}ms
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert" data-testid="reconcile-error">
          {error}
        </div>
      )}

      {report && (
        <>
          <section className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            <SummaryCard label="Total Records" value={report.total_records} accent="slate" />
            <SummaryCard label="Matched" value={report.matched_count} accent="emerald" />
            <SummaryCard label="Exceptions" value={report.exception_count} accent="red" />
            <SummaryCard label="Match Rate" value={formatPercent(report.match_rate)} accent="amber" />
            <SummaryCard label="Unresolved" value={report.unresolved_count} accent="slate" />
          </section>

          <section className="mt-6 grid gap-4 lg:grid-cols-2">
            <MatchBreakdownChart matchedCount={report.matched_count} exceptionCount={report.exception_count} />
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-medium text-slate-500">Run Details</h3>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-slate-500">Status</dt>
                  <dd>
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                      report.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                    }`}>
                      {report.status}
                    </span>
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Processing Time</dt>
                  <dd className="tabular-nums">{report.processing_time_ms.toFixed(3)} ms</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Throughput</dt>
                  <dd className="tabular-nums">
                    {Math.round(report.throughput_records_per_second).toLocaleString('en-IN')} rec/s
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Source</dt>
                  <dd className="font-medium">{source}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Seed / Size</dt>
                  <dd className="tabular-nums">{seed} / {size}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">AI Explanation</dt>
                  <dd>{explain ? 'Enabled' : 'Disabled'}</dd>
                </div>
              </dl>
            </div>
          </section>

          <PriorityBreakdown exceptionSummary={report.exception_summary} />

          {report.answer && (
            <section className="mt-6 rounded-xl border border-indigo-100 bg-indigo-50/60 p-5" data-testid="ai-explanation">
              <h3 className="text-sm font-semibold text-slate-900">AI Explanation</h3>
              <p className="mt-1 text-xs text-slate-500">
                Narrative only &mdash; the figures above remain the source of truth.
              </p>
              <div className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                {report.answer}
              </div>
            </section>
          )}

          {report.errors.length > 0 && (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
              {report.errors.map((e, i) => <p key={i}>{e}</p>)}
            </div>
          )}

          <WhatChangedPanel />
        </>
      )}
    </div>
  )
}

import { useState } from 'react'
import { postReconcileCompare } from '../api/client.js'
import { formatCompactMinor, formatMinor } from './format.js'

export default function WhatChangedPanel() {
  const [loading, setLoading] = useState(false)
  const [compareResult, setCompareResult] = useState(null)
  const [error, setError] = useState(null)

  const handleRunCompare = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await postReconcileCompare({
        previous_seed: 41,
        current_seed: 42,
        explain: true,
      })
      setCompareResult(data)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to perform drift comparison.'
      )
    } finally {
      setLoading(false)
    }
  }

  const drift = compareResult?.drift

  return (
    <section className="mt-8 rounded-xl border border-indigo-200 bg-white p-6 shadow-sm" data-testid="what-changed-section">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-100 pb-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            What Changed? (Drift Analysis)
          </h2>
          <p className="mt-0.5 text-sm text-slate-500">
            Deterministic run-over-run comparison (Run #41 vs Run #42)
          </p>
        </div>
        <button
          type="button"
          onClick={handleRunCompare}
          disabled={loading}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:bg-indigo-300"
          data-testid="run-compare-button"
        >
          {loading ? 'Analyzing Drift…' : compareResult ? 'Re-run Drift Analysis' : 'Run What-Changed Analysis'}
        </button>
      </div>

      {error ? (
        <p className="mt-4 text-sm text-red-600" data-testid="compare-error">
          {error}
        </p>
      ) : null}

      {drift ? (
        <div className="mt-5 space-y-6" data-testid="compare-results">
          {/* Key Change Indicators */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Match Rate Trend
              </span>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-xl font-bold text-slate-800 tabular-nums">
                  {drift.previous_match_rate != null ? `${drift.previous_match_rate}%` : '—'} → {drift.current_match_rate != null ? `${drift.current_match_rate}%` : '—'}
                </span>
                <span
                  className={`text-sm font-bold ${
                    (drift.match_rate_change_pp ?? 0) >= 0
                      ? 'text-emerald-600'
                      : 'text-red-600'
                  }`}
                >
                  {(drift.match_rate_change_pp ?? 0) >= 0 ? '+' : ''}
                  {drift.match_rate_change_pp}%
                </span>
              </div>
            </div>

            <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Exceptions Delta
              </span>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-xl font-bold text-slate-800 tabular-nums">
                  {drift.previous_exception_count} → {drift.current_exception_count}
                </span>
                <span
                  className={`text-sm font-bold ${
                    drift.exception_count_change <= 0
                      ? 'text-emerald-600'
                      : 'text-red-600'
                  }`}
                >
                  {drift.exception_count_change >= 0 ? '+' : ''}
                  {drift.exception_count_change}
                </span>
              </div>
            </div>

            <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Exposure Delta
              </span>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-xl font-bold text-slate-800 tabular-nums">
                  {formatCompactMinor(drift.previous_financial_exposure_minor)} → {formatCompactMinor(drift.current_financial_exposure_minor)}
                </span>
                <span
                  className={`text-sm font-bold ${
                    drift.financial_exposure_change_minor <= 0
                      ? 'text-emerald-600'
                      : 'text-red-600'
                  }`}
                >
                  {drift.financial_exposure_change_minor >= 0 ? '+' : ''}
                  {formatMinor(drift.financial_exposure_change_minor)}
                </span>
              </div>
            </div>
          </div>

          {/* Major Drivers */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Largest Drivers of Change
            </h3>
            <ul className="space-y-2">
              {drift.major_drivers.map((driver, idx) => (
                <li
                  key={idx}
                  className="flex items-start gap-2 text-sm text-slate-700 bg-indigo-50/40 border border-indigo-100 rounded-lg p-3"
                >
                  <span className="font-bold text-indigo-600 shrink-0">•</span>
                  <span>{driver}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* LLM Narrative if available */}
          {compareResult.answer ? (
            <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 p-4 text-sm leading-relaxed text-slate-700 whitespace-pre-line">
              <h4 className="font-semibold text-indigo-900 mb-1">AI Drift Narrative</h4>
              {compareResult.answer}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

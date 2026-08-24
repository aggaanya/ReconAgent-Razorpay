import { useMemo, useState } from 'react'
import EvidenceDetail from './EvidenceDetail.jsx'
import { formatDifference, formatMinor } from './format.js'

const STATUS_PILL = {
  AMOUNT_MISMATCH: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  MISSING_SETTLEMENT: 'bg-red-50 text-red-700 ring-red-600/20',
  MISSING_PAYMENT: 'bg-orange-50 text-orange-700 ring-orange-600/20',
  DUPLICATE_SETTLEMENT: 'bg-purple-50 text-purple-700 ring-purple-600/20',
  CURRENCY_MISMATCH: 'bg-sky-50 text-sky-700 ring-sky-600/20',
  INVALID_STATUS: 'bg-pink-50 text-pink-700 ring-pink-600/20',
  UNRESOLVED: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  UNEXPLAINED_SETTLEMENT_DIFFERENCE:
    'bg-yellow-50 text-yellow-800 ring-yellow-600/20',
  REFUND_MISMATCH: 'bg-rose-50 text-rose-700 ring-rose-600/20',
  SETTLEMENT_DELAY: 'bg-indigo-50 text-indigo-700 ring-indigo-600/20',
}

// Mirrors the backend's deterministic triage scale
// (app.services.reconciliation_policy): the server already computed
// severity/priority per row — this only styles and ranks what arrived.
const SEVERITY_PILL = {
  CRITICAL: 'bg-red-100 text-red-800 ring-red-600/30',
  HIGH: 'bg-orange-100 text-orange-800 ring-orange-600/30',
  MEDIUM: 'bg-amber-100 text-amber-800 ring-amber-600/30',
  LOW: 'bg-sky-100 text-sky-800 ring-sky-600/30',
  INFO: 'bg-slate-100 text-slate-600 ring-slate-500/20',
}

const SEVERITY_RANK = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0 }
const SEVERITIES = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

/**
 * The recorded components behind the expected settlement, when present.
 * Presentation only — every number comes straight from the engine's
 * result (gross − refunded − fee − tax = expected).
 */
function ComponentCell({ entry }) {
  const parts = []
  if (entry.gross_amount_minor !== null && entry.gross_amount_minor !== undefined) {
    parts.push(`G ${formatMinor(entry.gross_amount_minor, entry.currency)}`)
  }
  if (entry.refunded_total_minor) {
    parts.push(`R ${formatMinor(entry.refunded_total_minor, entry.currency)}`)
  }
  if (entry.fee_minor) parts.push(`F ${formatMinor(entry.fee_minor, entry.currency)}`)
  if (entry.tax_minor) parts.push(`T ${formatMinor(entry.tax_minor, entry.currency)}`)
  if (!parts.length) return <span className="text-slate-400">gross only</span>
  return (
    <span className="font-mono text-xs tabular-nums" title="G gross · R refunds · F fee · T tax">
      {parts.join(' · ')}
    </span>
  )
}

/**
 * The engine's typed exception list, verbatim. Formatting (paise to
 * rupees display) is presentation only — no values are derived here.
 * Severity/priority/recommended-action arrive precomputed from the
 * backend's deterministic triage policy and are only displayed, filtered
 * and sorted.
 */
export default function ExceptionTable({ exceptions }) {
  const [severityFilter, setSeverityFilter] = useState('ALL')
  const [categoryFilter, setCategoryFilter] = useState('ALL')
  const [sortField, setSortField] = useState('priority') // 'priority' | 'impact'
  const [sortDescending, setSortDescending] = useState(true)
  const [selectedKey, setSelectedKey] = useState(null)

  const rowKey = (e) =>
    `${e.source_transaction_id}-${e.matched_transaction_id ?? 'none'}`

  const categories = useMemo(() => {
    const set = new Set()
    exceptions.forEach((e) => {
      if (e.exception_type) set.add(e.exception_type)
    })
    return Array.from(set).sort()
  }, [exceptions])

  const visibleExceptions = useMemo(() => {
    let filtered = exceptions
    if (severityFilter !== 'ALL') {
      filtered = filtered.filter((e) => e.severity === severityFilter)
    }
    if (categoryFilter !== 'ALL') {
      filtered = filtered.filter((e) => e.exception_type === categoryFilter)
    }

    const rank = (e) => {
      if (sortField === 'impact') {
        return e.financial_impact_minor ?? 0
      }
      return typeof e.priority === 'number'
        ? e.priority
        : SEVERITY_RANK[e.severity] ?? -1
    }

    return [...filtered].sort((a, b) => {
      const delta = rank(b) - rank(a)
      if (delta !== 0) return sortDescending ? delta : -delta
      return a.source_transaction_id.localeCompare(b.source_transaction_id)
    })
  }, [exceptions, severityFilter, categoryFilter, sortField, sortDescending])

  const selectedException =
    visibleExceptions.find((e) => rowKey(e) === selectedKey) ?? null

  if (!exceptions.length) {
    return (
      <p className="text-sm text-slate-500" data-testid="exceptions-empty">
        No exceptions reported.
      </p>
    )
  }

  return (
    <>
      <div
        className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm"
        data-testid="exception-table"
      >
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-2 text-xs">
          <div className="flex items-center gap-1.5">
            <label
              htmlFor="severity-filter"
              className="font-medium uppercase tracking-wide text-slate-500"
            >
              Severity:
            </label>
            <select
              id="severity-filter"
              value={severityFilter}
              onChange={(event) => setSeverityFilter(event.target.value)}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 font-medium text-slate-700 focus:border-slate-500 focus:outline-none"
              data-testid="severity-filter"
            >
              <option value="ALL">All Severities</option>
              {SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {severity}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-1.5">
            <label
              htmlFor="category-filter"
              className="font-medium uppercase tracking-wide text-slate-500"
            >
              Type:
            </label>
            <select
              id="category-filter"
              value={categoryFilter}
              onChange={(event) => setCategoryFilter(event.target.value)}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 font-medium text-slate-700 focus:border-slate-500 focus:outline-none"
              data-testid="category-filter"
            >
              <option value="ALL">All Exception Types</option>
              {categories.map((cat) => (
                <option key={cat} value={cat}>
                  {cat}
                </option>
              ))}
            </select>
          </div>

          <span className="hidden text-slate-400 sm:inline">
            Click row to open evidence.
          </span>

          <div className="ml-auto flex items-center gap-2">
            <label className="font-medium uppercase tracking-wide text-slate-500">
              Sort by:
            </label>
            <button
              type="button"
              onClick={() => {
                if (sortField === 'priority') {
                  setSortDescending((curr) => !curr)
                } else {
                  setSortField('priority')
                  setSortDescending(true)
                }
              }}
              className={`rounded-md border px-2 py-1 font-medium ${
                sortField === 'priority'
                  ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                  : 'border-slate-200 text-slate-600 hover:border-slate-300'
              }`}
              data-testid="priority-sort"
            >
              Priority {sortField === 'priority' ? (sortDescending ? '↓' : '↑') : ''}
            </button>

            <button
              type="button"
              onClick={() => {
                if (sortField === 'impact') {
                  setSortDescending((curr) => !curr)
                } else {
                  setSortField('impact')
                  setSortDescending(true)
                }
              }}
              className={`rounded-md border px-2 py-1 font-medium ${
                sortField === 'impact'
                  ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                  : 'border-slate-200 text-slate-600 hover:border-slate-300'
              }`}
              data-testid="impact-sort"
            >
              Impact {sortField === 'impact' ? (sortDescending ? '↓' : '↑') : ''}
            </button>
          </div>
        </div>

        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Transaction</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Severity</th>
              <th className="px-4 py-3 text-right">Priority</th>
              <th className="px-4 py-3 text-right">Financial Impact</th>
              <th className="px-4 py-3">Components</th>
              <th className="px-4 py-3 text-right">Expected Amount</th>
              <th className="px-4 py-3 text-right">Actual Amount</th>
              <th className="px-4 py-3 text-right">Difference</th>
              <th className="px-4 py-3">Reason</th>
              <th className="px-4 py-3">Recommended Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visibleExceptions.map((e) => {
              const key = rowKey(e)
              const isSelected = key === selectedKey
              const isHighImpact =
                e.severity === 'CRITICAL' ||
                (e.financial_impact_minor && e.financial_impact_minor >= 1000000)

              return (
                <tr
                  key={key}
                  onClick={() =>
                    setSelectedKey((current) => (current === key ? null : key))
                  }
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      setSelectedKey((current) =>
                        current === key ? null : key,
                      )
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-selected={isSelected}
                  aria-expanded={isSelected}
                  data-testid={`exception-row-${e.source_transaction_id}`}
                  className={`cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                    isSelected
                      ? 'bg-indigo-50/70'
                      : isHighImpact
                      ? 'bg-red-50/10 hover:bg-slate-50'
                      : 'hover:bg-slate-50'
                  }`}
                >
                  <td className="px-4 py-3">
                    <div className="font-mono text-xs font-semibold text-slate-800">
                      {e.source_transaction_id}
                    </div>
                    {e.matched_transaction_id &&
                    e.matched_transaction_id !== e.source_transaction_id ? (
                      <div className="font-mono text-xs text-slate-400">
                        ↔ {e.matched_transaction_id}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
                        STATUS_PILL[e.status] ??
                        'bg-slate-100 text-slate-700 ring-slate-500/20'
                      }`}
                    >
                      {e.exception_type}
                    </span>
                    {Array.isArray(e.secondary_issues) &&
                    e.secondary_issues.length ? (
                      <div
                        className="mt-1 flex flex-wrap gap-1"
                        data-testid="secondary-issues"
                      >
                        {e.secondary_issues.map((issue) => (
                          <span
                            key={issue}
                            className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${
                              STATUS_PILL[issue] ??
                              'bg-slate-100 text-slate-700 ring-slate-500/20'
                            }`}
                          >
                            + {issue}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${
                        SEVERITY_PILL[e.severity] ??
                        'bg-slate-100 text-slate-600 ring-slate-500/20'
                      }`}
                      data-testid={`severity-${e.severity ?? 'unknown'}`}
                    >
                      {e.severity ?? '—'}
                    </span>
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono font-bold tabular-nums text-slate-800"
                    title="Deterministic triage priority score (0-100)"
                  >
                    {typeof e.priority === 'number' ? e.priority : '—'}
                  </td>
                  <td className="px-4 py-3 text-right font-mono font-bold tabular-nums text-red-600">
                    {e.financial_impact_minor != null
                      ? formatMinor(e.financial_impact_minor, e.currency)
                      : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <ComponentCell entry={e} />
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatMinor(e.expected_amount_minor, e.currency)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatMinor(e.actual_amount_minor, e.currency)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatDifference(e.difference_minor, e.currency)}
                  </td>
                  <td className="max-w-sm px-4 py-3 text-slate-600">
                    {e.reason}
                  </td>
                  <td
                    className="max-w-sm px-4 py-3 text-slate-600"
                    data-testid="recommended-action"
                  >
                    {e.recommended_action ?? '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {selectedException ? (
        <div className="mt-4" data-testid="evidence-panel-section">
          <EvidenceDetail exception={selectedException} />
        </div>
      ) : null}
    </>
  )
}

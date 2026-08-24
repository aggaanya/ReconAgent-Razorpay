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

function formatMinor(amount, currency = 'INR') {
  if (amount === null || amount === undefined) return '—'
  const formatter = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  })
  return formatter.format(amount / 100)
}

function formatDifference(minor, currency) {
  if (minor === null || minor === undefined) return '—'
  return `${minor > 0 ? '+' : ''}${formatMinor(minor, currency)}`
}

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
 */
export default function ExceptionTable({ exceptions }) {
  if (!exceptions.length) {
    return (
      <p className="text-sm text-slate-500" data-testid="exceptions-empty">
        No exceptions reported.
      </p>
    )
  }

  return (
    <div
      className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm"
      data-testid="exception-table"
    >
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-3">Transaction</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Components</th>
            <th className="px-4 py-3 text-right">Expected Amount</th>
            <th className="px-4 py-3 text-right">Actual Amount</th>
            <th className="px-4 py-3 text-right">Difference</th>
            <th className="px-4 py-3">Reason</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {exceptions.map((e) => (
            <tr key={`${e.source_transaction_id}-${e.matched_transaction_id ?? 'none'}`}>
              <td className="px-4 py-3">
                <div className="font-mono text-xs">{e.source_transaction_id}</div>
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
                    STATUS_PILL[e.status] ?? 'bg-slate-100 text-slate-700 ring-slate-500/20'
                  }`}
                >
                  {e.exception_type}
                </span>
                {Array.isArray(e.secondary_issues) && e.secondary_issues.length ? (
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
              <td className="max-w-sm px-4 py-3 text-slate-600">{e.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

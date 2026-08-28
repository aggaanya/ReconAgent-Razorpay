import { useReconciliation, RECONCILE_STATUS } from '../hooks/useReconciliation.js'
import SummaryCard from '../components/SummaryCard.jsx'
import MatchBreakdownChart from '../components/MatchBreakdownChart.jsx'
import PriorityBreakdown from '../components/PriorityBreakdown.jsx'
import { formatMinor } from '../components/format.js'

function formatPercent(value) {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)}%`
}

export default function OverviewPage() {
  const { status, report, error } = useReconciliation({
    source: 'synthetic',
    seed: 42,
    size: 100,
    explain: false,
  })

  const loading = status === RECONCILE_STATUS.RUNNING

  return (
    <div data-testid="overview-page">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-900">Overview</h1>
        <p className="mt-1 text-sm text-slate-500">
          Latest reconciliation run &mdash; synthetic demo dataset
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert">
          {error}
        </div>
      )}

      {loading && !report && (
        <div className="flex items-center justify-center py-20">
          <div className="text-sm text-slate-500">Loading dashboard...</div>
        </div>
      )}

      {report && (
        <>
          <section className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <SummaryCard label="Total Records" value={report.total_records} accent="slate" />
            <SummaryCard label="Matched" value={report.matched_count} accent="emerald" />
            <SummaryCard label="Exceptions" value={report.exception_count} accent="red" />
            <SummaryCard
              label="Match Rate"
              value={formatPercent(report.match_rate)}
              accent="amber"
            />
            <SummaryCard
              label="Financial Exposure"
              value={report.exception_summary ? formatMinor(report.exception_summary.total_financial_exposure_minor) : '—'}
              accent="red"
              hint="Total impact of unresolved exceptions"
            />
            <SummaryCard
              label="Critical Exceptions"
              value={report.exception_summary?.critical_count ?? 0}
              accent="red"
            />
            <SummaryCard
              label="High Priority"
              value={report.exception_summary?.high_count ?? 0}
              accent="amber"
            />
            <SummaryCard
              label="Unresolved"
              value={report.unresolved_count}
              accent="slate"
              hint="Requires human review"
            />
          </section>

          <section className="mt-6 grid gap-4 lg:grid-cols-2">
            <MatchBreakdownChart
              matchedCount={report.matched_count}
              exceptionCount={report.exception_count}
            />
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-medium text-slate-500">Exception Breakdown</h3>
              <div className="mt-4 space-y-2">
                {Object.entries(report.exception_breakdown)
                  .sort((a, b) => b[1] - a[1])
                  .map(([type, count]) => (
                    <div key={type} className="flex items-center justify-between text-sm">
                      <span className="font-mono text-xs text-slate-600">{type}</span>
                      <span className="font-semibold tabular-nums text-slate-900">{count}</span>
                    </div>
                  ))}
                {Object.keys(report.exception_breakdown).length === 0 && (
                  <p className="text-sm text-slate-400">No exceptions</p>
                )}
              </div>
            </div>
          </section>

          <PriorityBreakdown exceptionSummary={report.exception_summary} />

          <section className="mt-6 grid gap-4 lg:grid-cols-3">
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-medium text-slate-500">Processing</h3>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-slate-500">Status</dt>
                  <dd>
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                      report.status === 'completed'
                        ? 'bg-emerald-50 text-emerald-700'
                        : 'bg-amber-50 text-amber-700'
                    }`}>
                      {report.status}
                    </span>
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Processing Time</dt>
                  <dd className="tabular-nums font-medium">{report.processing_time_ms.toFixed(1)} ms</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Throughput</dt>
                  <dd className="tabular-nums font-medium">
                    {Math.round(report.throughput_records_per_second).toLocaleString('en-IN')} rec/s
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Unresolved</dt>
                  <dd className="tabular-nums font-medium">{report.unresolved_count}</dd>
                </div>
              </dl>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
              <h3 className="text-sm font-medium text-slate-500">Top Exception Categories</h3>
              <div className="mt-4 space-y-3">
                {report.exception_summary?.top_exception_categories?.slice(0, 5).map((cat, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <span className="text-slate-700">{cat.category ?? cat.type ?? 'Unknown'}</span>
                    <span className="font-mono text-xs tabular-nums text-slate-500">
                      {cat.count ?? 0} exceptions
                    </span>
                  </div>
                ))}
                {(!report.exception_summary?.top_exception_categories ||
                  report.exception_summary.top_exception_categories.length === 0) && (
                  <p className="text-sm text-slate-400">No top categories available</p>
                )}
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

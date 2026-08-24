import { formatCompactMinor, formatMinor } from './format.js'

export default function PriorityBreakdown({ exceptionSummary }) {
  if (!exceptionSummary) return null

  const {
    total_financial_exposure_minor = 0,
    critical_count = 0,
    critical_exposure_minor = 0,
    high_count = 0,
    high_exposure_minor = 0,
    medium_count = 0,
    medium_exposure_minor = 0,
    low_count = 0,
    low_exposure_minor = 0,
  } = exceptionSummary

  const cards = [
    {
      level: 'CRITICAL',
      count: critical_count,
      exposure: critical_exposure_minor,
      badgeStyle: 'bg-red-100 text-red-800 ring-red-600/30',
      cardStyle: 'border-red-200 bg-red-50/30',
      textColor: 'text-red-700',
    },
    {
      level: 'HIGH',
      count: high_count,
      exposure: high_exposure_minor,
      badgeStyle: 'bg-orange-100 text-orange-800 ring-orange-600/30',
      cardStyle: 'border-orange-200 bg-orange-50/30',
      textColor: 'text-orange-700',
    },
    {
      level: 'MEDIUM',
      count: medium_count,
      exposure: medium_exposure_minor,
      badgeStyle: 'bg-amber-100 text-amber-800 ring-amber-600/30',
      cardStyle: 'border-amber-200 bg-amber-50/30',
      textColor: 'text-amber-700',
    },
    {
      level: 'LOW',
      count: low_count,
      exposure: low_exposure_minor,
      badgeStyle: 'bg-sky-100 text-sky-800 ring-sky-600/30',
      cardStyle: 'border-sky-200 bg-sky-50/30',
      textColor: 'text-sky-700',
    },
  ]

  return (
    <div className="mt-6" data-testid="priority-breakdown">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-slate-900">
          Exception Prioritization & Financial Exposure
        </h2>
        <div className="flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-1.5 text-xs text-white">
          <span className="text-slate-300 uppercase tracking-wide font-medium">
            Total Exposure:
          </span>
          <span className="font-mono text-sm font-bold text-amber-400">
            {formatMinor(total_financial_exposure_minor)}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {cards.map((c) => (
          <div
            key={c.level}
            className={`rounded-xl border p-4 shadow-sm ${c.cardStyle}`}
            data-testid={`priority-card-${c.level}`}
          >
            <div className="flex items-center justify-between">
              <span
                className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-bold ring-1 ring-inset ${c.badgeStyle}`}
              >
                {c.level}
              </span>
              <span className="text-xs font-semibold text-slate-500">
                {c.count} {c.count === 1 ? 'exception' : 'exceptions'}
              </span>
            </div>
            <p className={`mt-3 text-2xl font-bold tabular-nums ${c.textColor}`}>
              {formatCompactMinor(c.exposure)}
            </p>
            <p className="mt-1 font-mono text-[11px] text-slate-500">
              {formatMinor(c.exposure)}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

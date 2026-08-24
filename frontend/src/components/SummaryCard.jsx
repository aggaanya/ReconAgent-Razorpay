const ACCENTS = {
  emerald: 'text-emerald-600',
  red: 'text-red-600',
  amber: 'text-amber-600',
  slate: 'text-slate-900',
}

export default function SummaryCard({ label, value, accent = 'slate', hint }) {
  return (
    <div
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      data-testid="summary-card"
    >
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${ACCENTS[accent]}`}>
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-slate-400">{hint}</p> : null}
    </div>
  )
}

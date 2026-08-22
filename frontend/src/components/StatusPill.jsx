const PILL_STYLES = {
  connected: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  unavailable: 'bg-red-50 text-red-700 ring-red-600/20',
  checking: 'bg-amber-50 text-amber-700 ring-amber-600/20',
}

const DOT_STYLES = {
  connected: 'bg-emerald-500',
  unavailable: 'bg-red-500',
  checking: 'bg-amber-500 animate-pulse',
}

export default function StatusPill({ status, label }) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ring-1 ring-inset ${PILL_STYLES[status]}`}
      data-testid="backend-status"
    >
      <span className={`h-2 w-2 rounded-full ${DOT_STYLES[status]}`} />
      {label}
    </span>
  )
}

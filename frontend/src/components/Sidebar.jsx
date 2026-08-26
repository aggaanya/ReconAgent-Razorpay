import {
  LayoutDashboard,
  GitCompareArrows,
  AlertTriangle,
  BrainCircuit,
  BarChart3,
  Settings,
} from './Icons.jsx'

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', Icon: LayoutDashboard },
  { id: 'reconciliation', label: 'Reconciliation', Icon: GitCompareArrows },
  { id: 'exceptions', label: 'Exceptions', Icon: AlertTriangle },
  { id: 'ai-insights', label: 'AI Insights', Icon: BrainCircuit },
  { id: 'evaluation', label: 'Evaluation', Icon: BarChart3 },
  { id: 'settings', label: 'Settings', Icon: Settings },
]

const STATUS_COLORS = {
  connected: 'bg-emerald-500',
  unavailable: 'bg-red-500',
  checking: 'bg-amber-500 animate-pulse',
}

const STATUS_LABELS = {
  connected: 'Backend connected',
  unavailable: 'Backend offline',
  checking: 'Checking...',
}

export default function Sidebar({ activePage, onNavigate, backendStatus }) {
  return (
    <aside
      className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col border-r border-slate-200 bg-white"
      data-testid="sidebar"
    >
      <div className="flex h-16 items-center gap-2.5 border-b border-slate-200 px-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-xs font-bold text-white">
          RA
        </div>
        <div>
          <span className="text-sm font-semibold tracking-tight text-slate-900">
            ReconAgent
          </span>
          <span className="block text-[10px] font-medium text-slate-400">
            AI Finance Intelligence
          </span>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {NAV_ITEMS.map(({ id, label, Icon }) => {
            const active = activePage === id
            return (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => onNavigate(id)}
                  className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                    active
                      ? 'bg-slate-900 text-white'
                      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                  }`}
                  data-testid={`nav-${id}`}
                  aria-current={active ? 'page' : undefined}
                >
                  <Icon
                    className={`h-4 w-4 shrink-0 ${
                      active ? 'text-slate-300' : 'text-slate-400'
                    }`}
                  />
                  {label}
                </button>
              </li>
            )
          })}
        </ul>
      </nav>

      <div className="border-t border-slate-200 px-5 py-4">
        <div className="flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${STATUS_COLORS[backendStatus] ?? STATUS_COLORS.checking}`}
          />
          <span className="text-xs font-medium text-slate-500">
            {STATUS_LABELS[backendStatus] ?? 'Unknown'}
          </span>
        </div>
      </div>
    </aside>
  )
}

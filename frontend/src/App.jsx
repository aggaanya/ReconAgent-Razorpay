import { useState } from 'react'
import AssistantPage from './pages/AssistantPage.jsx'
import ReconciliationPage from './pages/ReconciliationPage.jsx'
import SystemStatusPage from './pages/SystemStatusPage.jsx'

const TABS = [
  { id: 'reconciliation', label: 'Reconciliation' },
  { id: 'assistant', label: 'AI Assistant' },
  { id: 'system', label: 'System Status' },
]

export default function App() {
  const [tab, setTab] = useState('reconciliation')

  return (
    <div className="min-h-screen bg-slate-50">
      <nav
        className="border-b border-slate-200 bg-white"
        data-testid="app-nav"
      >
        <div className="mx-auto flex max-w-5xl items-center gap-1 px-6 py-3">
          <span className="mr-4 text-sm font-semibold tracking-tight text-slate-900">
            ReconAgent
          </span>
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              aria-pressed={tab === id}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                tab === id
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>
      {tab === 'reconciliation' ? (
        <ReconciliationPage />
      ) : tab === 'assistant' ? (
        <AssistantPage />
      ) : (
        <SystemStatusPage />
      )}
    </div>
  )
}

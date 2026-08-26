import { useState } from 'react'
import Sidebar from './components/Sidebar.jsx'
import { useBackendHealth } from './hooks/useBackendHealth.js'
import OverviewPage from './pages/OverviewPage.jsx'
import ReconciliationPage from './pages/ReconciliationPage.jsx'
import ExceptionsPage from './pages/ExceptionsPage.jsx'
import AiInsightsPage from './pages/AiInsightsPage.jsx'
import EvaluationPage from './pages/EvaluationPage.jsx'
import SettingsPage from './pages/SettingsPage.jsx'

const PAGES = {
  overview: OverviewPage,
  reconciliation: ReconciliationPage,
  exceptions: ExceptionsPage,
  'ai-insights': AiInsightsPage,
  evaluation: EvaluationPage,
  settings: SettingsPage,
}

export default function App() {
  const [page, setPage] = useState('overview')
  const { status: backendStatus } = useBackendHealth()

  const PageComponent = PAGES[page] ?? OverviewPage

  return (
    <div className="min-h-screen bg-slate-50">
      <Sidebar
        activePage={page}
        onNavigate={setPage}
        backendStatus={backendStatus}
      />
      <main className="ml-60 min-h-screen px-8 py-8">
        <PageComponent />
      </main>
    </div>
  )
}

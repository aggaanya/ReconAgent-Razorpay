import { useState } from 'react'
import useFinanceChat from '../hooks/useFinanceChat.js'
import { postAiReconcile } from '../api/client.js'

const SUGGESTIONS = [
  'What was my revenue this month?',
  'Show me the biggest financial issues.',
  'How much have I paid in fees and taxes?',
  'Are there any refunds that look suspicious?',
]

const STATUS_PILL = {
  completed: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  partial: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  failed: 'bg-red-50 text-red-700 ring-red-600/20',
}

function renderAnswer(text) {
  if (!text) return null
  const lines = text.split('\n')
  return lines.map((line, i) => {
    const trimmed = line.trim()
    if (!trimmed) return <br key={i} />
    if (/^#{1,3}\s/.test(trimmed)) {
      const level = trimmed.match(/^(#{1,3})/)[1].length
      const content = trimmed.replace(/^#{1,3}\s*/, '')
      const Tag = `h${level + 2}`
      return <Tag key={i} className="mt-4 text-base font-semibold text-slate-900">{content}</Tag>
    }
    if (/^[-*]\s/.test(trimmed)) {
      return (
        <li key={i} className="ml-4 list-disc text-sm text-slate-700">
          {formatInline(trimmed.replace(/^[-*]\s*/, ''))}
        </li>
      )
    }
    if (/^\d+\.\s/.test(trimmed)) {
      return (
        <li key={i} className="ml-4 list-decimal text-sm text-slate-700">
          {formatInline(trimmed.replace(/^\d+\.\s*/, ''))}
        </li>
      )
    }
    return <p key={i} className="text-sm leading-relaxed text-slate-700">{formatInline(trimmed)}</p>
  })
}

function formatInline(text) {
  const parts = text.split(/(\*\*[^*]+\*\*)/)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i} className="font-semibold text-slate-900">{part.slice(2, -2)}</strong>
    }
    return part
  })
}

export default function AiInsightsPage() {
  const [question, setQuestion] = useState('')
  const { data, isLoading, error, ask } = useFinanceChat()
  const [reconcileAnswer, setReconcileAnswer] = useState(null)
  const [reconcileLoading, setReconcileLoading] = useState(false)

  async function loadReconcileInsight() {
    setReconcileLoading(true)
    try {
      const data = await postAiReconcile({ source: 'synthetic', seed: 42, size: 100, explain: true })
      setReconcileAnswer(data)
    } catch {
      setReconcileAnswer(null)
    } finally {
      setReconcileLoading(false)
    }
  }

  function handleSubmit(e) {
    e.preventDefault()
    if (!question.trim() || isLoading) return
    ask(question).then(() => setQuestion(''))
  }

  return (
    <div data-testid="ai-insights-page">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">AI Insights</h1>
        <p className="mt-1 text-sm text-slate-500">
          AI-generated narratives over deterministic financial data.
          The agent plans tools, retrieves facts, and interprets &mdash; it never invents numbers.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">Reconciliation Explanation</h2>
          <p className="mt-1 text-xs text-slate-500">
            AI narrative for the latest reconciliation run (seed 42, 100 records).
          </p>
          <button
            type="button"
            onClick={loadReconcileInsight}
            disabled={reconcileLoading}
            className="mt-3 inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {reconcileLoading ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                Loading...
              </>
            ) : reconcileAnswer ? 'Refresh' : 'Load AI Explanation'}
          </button>

          {reconcileAnswer && (
            <div className="mt-4">
              {reconcileAnswer.answer ? (
                <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700" data-testid="reconcile-ai-answer">
                  {renderAnswer(reconcileAnswer.answer)}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">
                  No AI narrative available.
                  {reconcileAnswer.errors.length > 0 && ` ${reconcileAnswer.errors.join(' ')}`}
                </p>
              )}
            </div>
          )}
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">Finance Q&A</h2>
          <p className="mt-1 text-xs text-slate-500">
            Ask any question about your financial data. The agent selects the right tools and interprets the results.
          </p>

          <form onSubmit={handleSubmit} className="mt-4">
            <div className="flex gap-2">
              <input
                type="text"
                value={question}
                maxLength={1000}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask a finance question..."
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm placeholder:text-slate-400 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
              />
              <button
                type="submit"
                disabled={!question.trim() || isLoading}
                className="shrink-0 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {isLoading ? 'Thinking...' : 'Ask'}
              </button>
            </div>
          </form>

          <div className="mt-3 flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                disabled={isLoading}
                onClick={() => ask(s)}
                className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
              >
                {s}
              </button>
            ))}
          </div>

          <div className="mt-4 space-y-3">
            {isLoading && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                Planning tools and reading your books...
              </div>
            )}

            {error && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800" role="alert">
                {/503/.test(error)
                  ? 'The AI assistant needs an LLM key on the backend. Set LLM_API_KEY.'
                  : `The assistant could not answer: ${error}`}
              </div>
            )}

            {!isLoading && !error && !data && (
              <div className="rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
                Ask a question to see the agent plan tools, retrieve facts, and interpret them.
              </div>
            )}

            {data && (
              <div className="rounded-xl border border-slate-200 bg-white shadow-sm" data-testid="ai-chat-answer">
                <header className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
                  <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_PILL[data.status] ?? STATUS_PILL.failed}`}>
                    {data.status}
                  </span>
                  <span className="truncate text-xs text-slate-500">"{data.question}"</span>
                  {data.selected_tools?.length > 0 && (
                    <span className="ml-auto flex flex-wrap gap-1">
                      {data.selected_tools.map((tool) => (
                        <span key={tool} className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-600">
                          {tool}
                        </span>
                      ))}
                    </span>
                  )}
                </header>
                <div className="px-4 py-4">
                  {data.answer ? (
                    <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                      {renderAnswer(data.answer)}
                    </div>
                  ) : (
                    <p className="text-sm italic text-slate-500">
                      No narrative was produced, but any retrieved facts are shown below.
                    </p>
                  )}
                </div>
                {data.errors?.length > 0 && (
                  <footer className="border-t border-slate-100 px-4 py-3 text-xs text-amber-700">
                    {data.errors.map((msg, i) => <p key={i}>{msg}</p>)}
                  </footer>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

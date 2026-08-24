import { useState } from 'react'
import useFinanceChat from '../hooks/useFinanceChat.js'

const STATUS_PILL = {
  completed: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  partial: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  failed: 'bg-red-50 text-red-700 ring-red-600/20',
}

const SUGGESTIONS = [
  'What was my revenue this month?',
  'Show me the biggest financial issues.',
  'How much have I paid in fees and taxes?',
  'Are there any refunds that look suspicious?',
]

function ErrorBanner({ message }) {
  const notConfigured = /503/.test(message)
  return (
    <div
      className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800"
      data-testid="assistant-error"
      role="alert"
    >
      {notConfigured
        ? 'The AI assistant needs an LLM key on the backend (set LLM_API_KEY) before questions can be answered. Everything else on this dashboard runs without credentials.'
        : `The assistant could not answer: ${message}`}
    </div>
  )
}

/**
 * Conversational front end for the existing LangGraph finance agent
 * (POST /api/v1/ai/chat). The backend plans tools, runs deterministic
 * Finance Tools, derives signals, and only then interprets — this page
 * renders that outcome without recomputing anything.
 */
export default function AssistantPage() {
  const [question, setQuestion] = useState('')
  const { data, isLoading, error, ask } = useFinanceChat()

  function handleSubmit(event) {
    event.preventDefault()
    if (!question.trim() || isLoading) return
    ask(question).then(() => setQuestion(''))
  }

  return (
    <main
      className="mx-auto max-w-4xl px-6 py-8"
      data-testid="assistant-page"
    >
      <h1 className="text-lg font-semibold tracking-tight text-slate-900">
        AI Finance Assistant
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Answers are narratives over deterministic tool output. The agent may
        run reconciliation, metrics and signal tools before interpreting —
        it never invents numbers.
      </p>

      <form onSubmit={handleSubmit} className="mt-5">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="text"
            value={question}
            maxLength={1000}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask a finance question…"
            aria-label="Finance question"
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm placeholder:text-slate-400 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
            data-testid="assistant-input"
          />
          <button
            type="submit"
            disabled={!question.trim() || isLoading}
            className="shrink-0 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            data-testid="assistant-submit"
          >
            {isLoading ? 'Thinking…' : 'Ask'}
          </button>
        </div>
      </form>

      <div className="mt-3 flex flex-wrap gap-2">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            disabled={isLoading}
            onClick={() => ask(suggestion)}
            className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 shadow-sm hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
            data-testid="assistant-suggestion"
          >
            {suggestion}
          </button>
        ))}
      </div>

      <div className="mt-6 space-y-4" aria-live="polite">
        {error ? <ErrorBanner message={error} /> : null}

        {isLoading ? (
          <div
            className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-500 shadow-sm"
            data-testid="assistant-loading"
          >
            Planning tools and reading your books…
          </div>
        ) : null}

        {!isLoading && !error && !data ? (
          <div
            className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500"
            data-testid="assistant-empty"
          >
            Ask a question to see the agent plan tools, retrieve facts from
            your ledger, and interpret them.
          </div>
        ) : null}

        {data ? (
          <section
            className="rounded-xl border border-slate-200 bg-white shadow-sm"
            data-testid="assistant-answer"
          >
            <header className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
              <span
                className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
                  STATUS_PILL[data.status] ?? STATUS_PILL.failed
                }`}
                data-testid="assistant-status"
              >
                {data.status}
              </span>
              <span className="truncate text-xs text-slate-500">
                “{data.question}”
              </span>
              {Array.isArray(data.selected_tools) &&
              data.selected_tools.length ? (
                <span
                  className="ml-auto flex flex-wrap gap-1"
                  data-testid="assistant-tools"
                  title={`Tool selection: ${data.selection_source || 'planner'}`}
                >
                  {data.selected_tools.map((tool) => (
                    <span
                      key={tool}
                      className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-600"
                    >
                      {tool}
                    </span>
                  ))}
                </span>
              ) : null}
            </header>

            <div className="px-4 py-4">
              {data.answer ? (
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                  {data.answer}
                </p>
              ) : (
                <p className="text-sm italic text-slate-500">
                  No narrative was produced, but any retrieved facts are
                  shown below.
                </p>
              )}
            </div>

            {Array.isArray(data.financial_signals) &&
            data.financial_signals.length ? (
              <footer
                className="border-t border-slate-100 px-4 py-3 text-xs text-slate-500"
                data-testid="assistant-signals"
              >
                {data.financial_signals.length} financial signal
                {data.financial_signals.length === 1 ? '' : 's'} derived by
                the Signal Analysis Engine.
              </footer>
            ) : null}

            {(data.errors?.length || Object.keys(data.tool_errors ?? {}).length) ? (
              <footer className="space-y-1 border-t border-slate-100 px-4 py-3 text-xs text-amber-700">
                {(data.errors ?? []).map((message, index) => (
                  <p key={`error-${index}`}>{message}</p>
                ))}
                {Object.entries(data.tool_errors ?? {}).map(
                  ([tool, message]) => (
                    <p key={`tool-${tool}`}>
                      {tool}: {message}
                    </p>
                  ),
                )}
              </footer>
            ) : null}
          </section>
        ) : null}
      </div>
    </main>
  )
}

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

/** Matched vs exceptions, straight from the engine's summary. */
export default function MatchBreakdownChart({ matchedCount, exceptionCount }) {
  const data = [
    { name: 'Matched', count: matchedCount },
    { name: 'Exceptions', count: exceptionCount },
  ]

  return (
    <div
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      data-testid="match-breakdown-chart"
    >
      <h3 className="text-sm font-medium text-slate-500">
        Matched vs Exceptions
      </h3>
      <div className="mt-4 h-56">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="name" tickLine={false} />
            <YAxis allowDecimals={false} tickLine={false} />
            <Tooltip />
            <Legend />
            <Bar dataKey="count" name="Records" fill="#0ea5e9" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

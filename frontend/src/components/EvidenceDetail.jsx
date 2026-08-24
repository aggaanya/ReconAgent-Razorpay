import { formatDifference, formatMinor } from './format.js'

function EvidenceRow({ label, children, mono = false }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="shrink-0 text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd
        className={`text-right text-sm text-slate-800 ${mono ? 'font-mono tabular-nums' : ''}`}
      >
        {children ?? '—'}
      </dd>
    </div>
  )
}

function Money({ amount, currency }) {
  if (amount === null || amount === undefined) return '—'
  return formatMinor(amount, currency)
}

function SectionTitle({ children }) {
  return (
    <h4 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400 first:mt-0">
      {children}
    </h4>
  )
}

/**
 * Detail panel for one selected exception row. Every figure is rendered
 * verbatim from the backend's structured evidence (falling back to the
 * row's summary fields when a payload predates the evidence pass) —
 * nothing is derived or recalculated here; paise→rupees conversion is
 * display formatting only.
 */
export default function EvidenceDetail({ exception }) {
  const ev = exception.evidence ?? null
  const currency = exception.currency ?? 'INR'

  const paymentLabel =
    ev?.payment_id != null
      ? [
          ev.payment_id,
          ev.payment_amount_minor != null
            ? formatMinor(ev.payment_amount_minor, currency)
            : null,
        ]
          .filter(Boolean)
          .join(' · ')
      : null
  const settlementId = ev?.settlement_id ?? exception.matched_transaction_id ?? null
  const settlementAmount =
    ev?.settlement_amount_minor ?? exception.actual_amount_minor ?? null
  const refunds = Array.isArray(ev?.refunds) ? ev.refunds : []
  const fee = ev ? ev.fee_minor : (exception.fee_minor ?? null)
  const tax = ev ? ev.tax_minor : (exception.tax_minor ?? null)
  const gross = ev
    ? ev.gross_amount_minor
    : (exception.gross_amount_minor ?? null)
  const refundedTotal = ev
    ? ev.refunded_total_minor
    : (exception.refunded_total_minor ?? null)
  const expected =
    ev?.expected_settlement_minor ?? exception.expected_amount_minor ?? null
  const actual =
    ev?.actual_settlement_minor ?? exception.actual_amount_minor ?? null
  const difference = ev?.difference_minor ?? exception.difference_minor ?? null
  const rulesTriggered = [
    ...(ev?.rules_triggered ??
      (exception.exception_type ? [exception.exception_type] : [])),
    ...(ev
      ? []
      : Array.isArray(exception.secondary_issues)
        ? exception.secondary_issues
        : []),
  ]

  return (
    <div
      className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      data-testid="evidence-detail"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-900">
          Evidence detail{' '}
          <span className="font-mono text-xs font-normal text-slate-500">
            {exception.source_transaction_id}
          </span>
        </h3>
        {!ev ? (
          <span
            className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500"
            title="Payload was produced without the backend evidence pass"
            data-testid="evidence-fallback-note"
          >
            summary fields only
          </span>
        ) : null}
      </div>

      <dl className="mt-3 divide-y divide-slate-100">
        <SectionTitle>Payment</SectionTitle>
        <EvidenceRow label="Payment" mono>
          {paymentLabel}
        </EvidenceRow>
        {gross != null ? (
          <EvidenceRow label="Gross Amount" mono>
            <Money amount={gross} currency={currency} />
          </EvidenceRow>
        ) : null}

        <SectionTitle>Settlement</SectionTitle>
        <EvidenceRow label="Settlement" mono>
          {settlementId}
        </EvidenceRow>
        <EvidenceRow label="Settled Amount" mono>
          <Money amount={settlementAmount} currency={currency} />
        </EvidenceRow>
        {ev && ev.additional_settlement_ids.length ? (
          <EvidenceRow label="Additional Settlements" mono>
            {ev.additional_settlement_ids.join(', ')}
          </EvidenceRow>
        ) : null}

        <SectionTitle>Refunds</SectionTitle>
        {refunds.length
          ? refunds.map((refund) => (
              <EvidenceRow
                key={refund.refund_id}
                label={refund.refund_id}
                mono
              >
                {[
                  formatMinor(refund.amount_minor, refund.currency ?? currency),
                  refund.status,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </EvidenceRow>
            ))
          : (
              <EvidenceRow label="Refunds" mono>
                {refundedTotal
                  ? `${formatMinor(refundedTotal, currency)} (total)`
                  : 'None recorded'}
              </EvidenceRow>
            )}

        <SectionTitle>Components</SectionTitle>
        <EvidenceRow label="Fee" mono>
          <Money amount={fee} currency={currency} />
        </EvidenceRow>
        <EvidenceRow label="Tax" mono>
          <Money amount={tax} currency={currency} />
        </EvidenceRow>

        <SectionTitle>Financial Impact & Priority</SectionTitle>
        <EvidenceRow label="Financial Exposure" mono>
          <span className="font-bold text-red-600">
            <Money
              amount={ev?.financial_impact_minor ?? exception.financial_impact_minor}
              currency={currency}
            />
          </span>
        </EvidenceRow>
        <EvidenceRow label="Triage Priority" mono>
          <span className="font-bold text-slate-900">
            {exception.priority ?? '—'} (Severity: {exception.severity ?? '—'})
          </span>
        </EvidenceRow>

        <SectionTitle>Reconciliation</SectionTitle>
        <EvidenceRow label="Expected Settlement" mono>
          <Money amount={expected} currency={currency} />
        </EvidenceRow>
        <EvidenceRow label="Actual Settlement" mono>
          <Money amount={actual} currency={currency} />
        </EvidenceRow>
        <EvidenceRow label="Difference" mono>
          <span
            className={
              typeof difference === 'number' && difference !== 0
                ? 'font-semibold text-red-700'
                : ''
            }
          >
            {formatDifference(difference, currency)}
          </span>
        </EvidenceRow>
        <EvidenceRow label="Rules Triggered">
          {rulesTriggered.length ? (
            <span className="flex flex-wrap justify-end gap-1">
              {rulesTriggered.map((rule) => (
                <span
                  key={rule}
                  className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-500/20"
                >
                  {rule}
                </span>
              ))}
            </span>
          ) : null}
        </EvidenceRow>
        <EvidenceRow label="Exception Status" mono>
          {exception.status}
        </EvidenceRow>
      </dl>

      <div className="mt-3 border-t border-slate-100 pt-3">
        <h4 className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Impact Explanation
        </h4>
        <p className="mt-0.5 text-xs text-slate-700 font-mono">
          {ev?.financial_impact_reason ?? exception.financial_impact_reason ?? '—'}
        </p>

        <h4 className="mt-3 text-xs font-medium uppercase tracking-wide text-slate-500">
          Existing Reason
        </h4>
        <p
          className="mt-1 text-sm leading-relaxed text-slate-600"
          data-testid="evidence-reason"
        >
          {ev?.reason ?? exception.reason}
        </p>
      </div>
    </div>
  )
}

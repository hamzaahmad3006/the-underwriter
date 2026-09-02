import { Link, useParams } from 'react-router-dom'

import { Badge } from '../../../components/ui/Badge'
import { type Column, DataTable } from '../../../components/ui/DataTable'
import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { Stat } from '../../../components/ui/Stat'
import { type LegRow, type OrderRow, usePolicy } from './usePolicy'

/**
 * §21.7 — the policy lifecycle, as a stepper.
 *
 * A failed policy shows the step it died at rather than disappearing, because
 * the interesting question about a policy that never opened is where it
 * stopped: the model declined, the Kernel vetoed, or the order never filled.
 */
const LIFECYCLE = [
  'Candidate',
  'Underwritten',
  'Kernel-Approved',
  'Executed',
  'Managed',
  'Closing',
  'Settled',
] as const

const STATUS_STEP: Record<string, number> = {
  PENDING: 2,
  OPEN: 4,
  LEG_RISK: 3,
  CLOSING: 5,
  SETTLED: 6,
  FAILED: 3,
}

const LEG_COLUMNS: Column<LegRow>[] = [
  { key: 'symbol', header: 'Contract', render: (r) => <span className="num">{r.symbol}</span> },
  { key: 'side', header: 'Side', render: (r) => <Badge tone="neutral">{r.side}</Badge> },
  { key: 'intent', header: 'Intent', render: (r) => r.position_intent ?? '—' },
  { key: 'strike', header: 'Strike', numeric: true, render: (r) => r.strike ?? '—' },
  { key: 'open', header: 'Open price', numeric: true, render: (r) => r.open_price ?? '—' },
  { key: 'delta', header: 'Open delta', numeric: true, render: (r) => r.open_delta ?? '—' },
]

const ORDER_COLUMNS: Column<OrderRow>[] = [
  { key: 'coid', header: 'Client order id', render: (r) => <span className="num">{r.client_order_id}</span> },
  { key: 'intent', header: 'Intent', render: (r) => r.intent },
  { key: 'status', header: 'Status', render: (r) => <Badge tone="neutral">{r.status}</Badge> },
  { key: 'limit', header: 'Limit', numeric: true, render: (r) => r.limit_price ?? '—' },
  { key: 'filled', header: 'Filled', numeric: true, render: (r) => r.filled_qty ?? '—' },
  {
    key: 'verdict',
    header: 'Authorised by',
    render: (r) => (
      <span className="num text-faint" title="NFR-008: no order row exists without one">
        {r.kernel_decision_id ? `${r.kernel_decision_id.slice(0, 8)}…` : '—'}
      </span>
    ),
  },
]

function Stepper({ status }: { status: string }) {
  const reached = STATUS_STEP[status] ?? 0
  const failed = status === 'FAILED' || status === 'LEG_RISK'

  return (
    <ol className="-mx-4 flex gap-1 overflow-x-auto px-4 pb-1">
      {LIFECYCLE.map((step, index) => {
        const done = index <= reached
        const here = index === reached
        const tone = here && failed ? 'border-critical text-critical' : done ? 'border-accent text-ink' : 'border-line text-faint'

        return (
          <li
            key={step}
            className={`shrink-0 rounded border px-2.5 py-1 text-[11px] font-medium whitespace-nowrap ${tone}`}
          >
            {step}
          </li>
        )
      })}
    </ol>
  )
}

export function PolicyDetail() {
  const { policyId = '' } = useParams()
  const { data, isLoading, error } = usePolicy(policyId)

  return (
    <>
      <div className="flex items-center gap-3">
        <Link to="/dashboard/book" className="text-muted hover:text-ink text-xs">
          ← Back to the book
        </Link>
      </div>

      <Panel
        title={data ? `Policy ${data.policy.policy_number}` : 'Policy'}
        asOf={data?.as_of ?? null}
        actions={data ? <Badge tone="neutral">{data.policy.status}</Badge> : undefined}
      >
        {isLoading && <Skeleton rows={4} />}
        {error && <ErrorState error={error} what="this policy" />}

        {data && (
          <div className="space-y-6">
            <Stepper status={data.policy.status} />

            <dl className="grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-3 lg:grid-cols-5">
              <Stat label="Underlying" value={data.policy.underlying} tone="muted" />
              <Stat label="Contracts" value={String(data.policy.contracts)} />
              <Stat label="Credit" value={data.policy.opening_credit ?? '—'} />
              <Stat label="Max loss" value={data.policy.max_loss ?? '—'} />
              <Stat
                label="Realised"
                value={data.policy.realized_pnl ?? '—'}
                tone={
                  data.policy.realized_pnl === null
                    ? 'muted'
                    : Number(data.policy.realized_pnl) < 0
                      ? 'critical'
                      : 'positive'
                }
                hint={data.policy.settlement_reason ?? undefined}
              />
            </dl>

            {data.policy.predicted_confidence && (
              <p className="text-faint text-xs leading-relaxed">
                The model stated {data.policy.predicted_confidence} confidence before the outcome
                was known (FR-044). It is scored against what actually happened, so overconfidence
                shows up rather than washing out.
              </p>
            )}
          </div>
        )}
      </Panel>

      <Panel title="Legs" asOf={data?.as_of ?? null}>
        {isLoading && <Skeleton rows={2} />}
        {data && <DataTable columns={LEG_COLUMNS} rows={data.legs} rowKey={(r) => r.symbol} />}
      </Panel>

      <Panel title="Orders" asOf={data?.as_of ?? null}>
        <p className="text-muted mb-4 text-xs leading-relaxed">
          Every order carries the verdict that authorised it. That column cannot be empty: the
          column is <code className="num">NOT NULL</code> in the schema, so an order with no
          verdict behind it has no row shape to exist in.
        </p>
        {isLoading && <Skeleton rows={2} />}
        {data && (
          <DataTable columns={ORDER_COLUMNS} rows={data.orders} rowKey={(r) => r.id} />
        )}
      </Panel>
    </>
  )
}

import { Badge } from '../../../components/ui/Badge'
import { DataTable, type Column } from '../../../components/ui/DataTable'
import { EmptyState } from '../../../components/ui/EmptyState'
import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { Link } from 'react-router-dom'

import type { PolicyRow } from '../../../api/types'
import { type CandidateRow, type DiscardRow, useBook } from './useBook'

const STATUS_TONE = {
  OPEN: 'positive',
  CLOSING: 'caution',
  SETTLED: 'neutral',
  PENDING: 'neutral',
  FAILED: 'critical',
  LEG_RISK: 'critical',
} as const

const POLICY_COLUMNS: Column<PolicyRow>[] = [
  {
    key: 'no',
    header: 'Policy',
    render: (r) => (
      <Link to={`/dashboard/book/${r.id}`} className="num text-accent hover:underline">
        {r.policy_number}
      </Link>
    ),
  },
  { key: 'sym', header: 'Underlying', render: (r) => r.underlying },
  {
    key: 'status',
    header: 'Status',
    render: (r) => <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>,
  },
  { key: 'contracts', header: 'Size', numeric: true, render: (r) => r.contracts },
  { key: 'credit', header: 'Credit', numeric: true, render: (r) => r.opening_credit ?? '—' },
  { key: 'reserve', header: 'Reserved', numeric: true, render: (r) => r.capital_reserve ?? '—' },
  { key: 'exp', header: 'Expiry', render: (r) => r.expiration ?? '—' },
  {
    key: 'pnl',
    header: 'Realised',
    numeric: true,
    render: (r) =>
      r.realized_pnl === null ? (
        <span className="text-faint">—</span>
      ) : (
        <span className={Number(r.realized_pnl) < 0 ? 'text-critical' : 'text-positive'}>
          {r.realized_pnl}
        </span>
      ),
  },
]

const CANDIDATE_COLUMNS: Column<CandidateRow>[] = [
  { key: 'sym', header: 'Underlying', render: (r) => r.underlying },
  {
    key: 'spread',
    header: 'Spread',
    numeric: true,
    render: (r) => `${r.short_strike} / ${r.long_strike}`,
  },
  { key: 'dte', header: 'DTE', numeric: true, render: (r) => r.dte },
  { key: 'credit', header: 'Credit', numeric: true, render: (r) => r.net_credit },
  { key: 'maxloss', header: 'Max loss', numeric: true, render: (r) => `$${r.max_loss}` },
  { key: 'edge', header: 'Edge', numeric: true, render: (r) => r.edge_ratio },
  { key: 'liq', header: 'Liquidity', numeric: true, render: (r) => r.liquidity_score },
  {
    key: 'delta',
    header: 'Short delta',
    numeric: true,
    render: (r) => (
      <span title="Delta-implied and approximate (NG-02)">{r.short_delta}</span>
    ),
  },
]

const DISCARD_COLUMNS: Column<DiscardRow>[] = [
  { key: 'id', header: 'Candidate', render: (r) => r.candidate_id },
  { key: 'reason', header: 'Reason', render: (r) => <Badge tone="caution">{r.reason}</Badge> },
  { key: 'detail', header: 'Detail', render: (r) => <span className="text-muted">{r.detail}</span> },
]

export function Book() {
  const { policies, candidates } = useBook()
  const set = candidates.data

  return (
    <>
      <Panel
        title="The book — API-020"
        asOf={policies.data?.as_of ?? null}
        actions={
          policies.data && !policies.data.empty ? (
            <span className="text-faint num text-[11px]">{policies.data.total} written</span>
          ) : undefined
        }
      >
        {policies.isLoading && <Skeleton rows={4} />}
        {policies.error && <ErrorState error={policies.error} what="the underwriting book" />}
        {policies.data && (
          <DataTable
            columns={POLICY_COLUMNS}
            rows={policies.data.policies}
            rowKey={(r) => r.id}
            empty={
              <EmptyState
                title="No policies written"
                reason="No underwriting cycle has produced an executed policy in this database."
                hint="The candidate table below shows what the Actuary is looking at right now."
              />
            }
          />
        )}
      </Panel>

      <Panel
        title="Candidates the Actuary priced this cycle — API-030"
        asOf={set?.as_of ?? null}
        actions={
          set?.aborted ? <Badge tone="caution">{set.aborted}</Badge> : undefined
        }
      >
        <p className="text-muted mb-4 text-xs leading-relaxed">
          Live: this runs the real market data layer and the real Actuary. Credit is quoted
          conservatively — short bid, long ask — so every figure below already assumes the worse
          fill on both legs.
        </p>

        {candidates.isLoading && <Skeleton rows={5} />}
        {candidates.error && <ErrorState error={candidates.error} what="the candidate set" />}

        {set?.aborted && (
          <EmptyState
            title="No candidates this cycle"
            reason={set.detail}
            hint="An aborted cycle is a recorded outcome, not a failure (FR-026)."
          />
        )}

        {set && !set.aborted && (
          <div className="space-y-6">
            <DataTable
              columns={CANDIDATE_COLUMNS}
              rows={set.candidates}
              rowKey={(r) => r.candidate_id}
              empty={
                <EmptyState
                  title="Nothing qualified"
                  reason={`${set.discards.length} candidates were priced and every one was discarded.`}
                  hint="A cycle that writes nothing is a successful cycle."
                />
              }
            />

            {set.discards.length > 0 && (
              <div>
                <h3 className="text-muted mb-2 text-[11px] font-medium tracking-wider uppercase">
                  Discarded ({set.discards.length})
                </h3>
                <DataTable
                  columns={DISCARD_COLUMNS}
                  rows={set.discards.slice(0, 25)}
                  rowKey={(r) => `${r.candidate_id}-${r.reason}`}
                />
              </div>
            )}
          </div>
        )}
      </Panel>
    </>
  )
}

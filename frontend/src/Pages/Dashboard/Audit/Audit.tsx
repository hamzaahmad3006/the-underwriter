import { Badge } from '../../../components/ui/Badge'
import { type Column, DataTable } from '../../../components/ui/DataTable'
import { EmptyState } from '../../../components/ui/EmptyState'
import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { Stat } from '../../../components/ui/Stat'
import { type AuditRecord, useAudit } from './useAudit'

const COLUMNS: Column<AuditRecord>[] = [
  { key: 'seq', header: 'Seq', numeric: true, render: (r) => r.seq },
  {
    key: 'when',
    header: 'Occurred',
    render: (r) => (
      <span className="num text-muted">{new Date(r.occurred_at).toLocaleString()}</span>
    ),
  },
  { key: 'actor', header: 'Actor', render: (r) => <Badge tone="neutral">{r.actor}</Badge> },
  { key: 'action', header: 'Action', render: (r) => r.action },
  {
    key: 'hash',
    header: 'Record hash',
    render: (r) => <span className="num text-faint">{r.record_hash.slice(0, 12)}…</span>,
  },
]

export function Audit() {
  const { verification, log } = useAudit()
  const chain = verification.data

  return (
    <>
      <Panel
        title="Hash chain — API-061"
        asOf={chain?.as_of ?? null}
        actions={
          chain ? (
            <Badge tone={chain.valid ? 'positive' : 'critical'}>
              {chain.valid ? 'VERIFIED' : 'BROKEN'}
            </Badge>
          ) : undefined
        }
      >
        <p className="text-muted mb-4 text-xs leading-relaxed">
          Every record hashes its own content together with the previous record&apos;s hash, so
          editing history breaks every hash after the edit. This walks the whole ledger from
          genesis and names the first sequence number where the chain fails.
        </p>

        {verification.isLoading && <Skeleton rows={3} />}
        {verification.error && <ErrorState error={verification.error} what="chain verification" />}

        {chain && (
          <>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Stat
                label="Chain"
                value={chain.valid ? 'intact' : 'broken'}
                tone={chain.valid ? 'positive' : 'critical'}
              />
              <Stat label="Records checked" value={String(chain.records_checked)} />
              <Stat
                label="First break"
                value={chain.first_break_seq === null ? 'none' : `seq ${chain.first_break_seq}`}
                tone={chain.first_break_seq === null ? 'muted' : 'critical'}
              />
            </dl>
            <p className="text-faint mt-4 text-xs">{chain.detail}</p>
          </>
        )}
      </Panel>

      <Panel title="Audit trail — API-060" asOf={log.data?.as_of ?? null}>
        {log.isLoading && <Skeleton rows={5} />}
        {log.error && <ErrorState error={log.error} what="the audit trail" />}
        {log.data && (
          <DataTable
            columns={COLUMNS}
            rows={log.data.records}
            rowKey={(r) => String(r.seq)}
            empty={
              <EmptyState
                title="The ledger is empty"
                reason="No cycle has run yet, so there is nothing to record."
                hint="Records appear here the moment the scheduler completes its first cycle."
              />
            }
          />
        )}
      </Panel>
    </>
  )
}

import type { RuleSpec } from '../../../api/types'
import { Badge } from '../../../components/ui/Badge'
import { type Column, DataTable } from '../../../components/ui/DataTable'
import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { useRisk } from './useRisk'

const RULE_COLUMNS: Column<RuleSpec>[] = [
  { key: 'id', header: 'Rule', render: (r) => <span className="num">{r.rule_id}</span> },
  { key: 'name', header: 'Name', render: (r) => r.name.replace(/_/g, ' ') },
  {
    key: 'severity',
    header: 'Severity',
    render: (r) =>
      r.severity === 'HARD' ? (
        <Badge tone="critical">HARD</Badge>
      ) : (
        <Badge tone="caution">SOFT</Badge>
      ),
  },
]

export function Risk() {
  const { limits, exposure, events } = useRisk()

  return (
    <>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Exposure and headroom — API-040"
          asOf={exposure.data?.as_of ?? null}
          actions={
            exposure.data ? (
              <Badge tone={exposure.data.reserve_invariant.holds ? 'positive' : 'critical'}>
                {exposure.data.reserve_invariant.holds ? 'DB-INV-1 OK' : 'DB-INV-1 BROKEN'}
              </Badge>
            ) : undefined
          }
        >
          {exposure.isLoading && <Skeleton rows={4} />}
          {exposure.error && <ErrorState error={exposure.error} what="portfolio exposure" />}
          {exposure.data && (
            <div className="space-y-4">
              <dl className="space-y-2">
                {Object.entries(exposure.data.limits).map(([name, room]) => (
                  <div key={name}>
                    <div className="flex items-baseline justify-between gap-3 text-xs">
                      <dt className="text-muted truncate">{name.replace(/_/g, ' ')}</dt>
                      <dd className="num shrink-0">
                        {room.used} / {room.limit}
                      </dd>
                    </div>
                    {room.utilization_pct && (
                      <div className="bg-raised mt-1 h-1 w-full overflow-hidden rounded">
                        <div
                          className="bg-accent h-full"
                          style={{
                            width: `${Math.min(100, Number(room.utilization_pct))}%`,
                          }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </dl>

              <p
                className={`text-xs leading-relaxed ${
                  exposure.data.reserve_invariant.holds ? 'text-muted' : 'text-critical'
                }`}
              >
                {exposure.data.reserve_invariant.detail}
              </p>
              <p className="text-faint text-[11px] leading-relaxed">
                DB-INV-1: held reserves must equal the max loss across open policies. If it fails,
                the book&apos;s own accounting disagrees with itself and every capital limit above
                is measuring the wrong number.
              </p>
            </div>
          )}
        </Panel>

        <Panel title="Risk events — API-042" asOf={null}>
          {events.isLoading && <Skeleton rows={4} />}
          {events.error && <ErrorState error={events.error} what="the risk event feed" />}
          {events.data != null && (
            <pre className="num text-muted overflow-x-auto text-[11px]">
              {JSON.stringify(events.data, null, 2)}
            </pre>
          )}
        </Panel>
      </div>

      <Panel title="Configured limits — API-041" asOf={limits.data?.as_of ?? null}>
        {limits.isLoading && <Skeleton rows={6} />}
        {limits.error && <ErrorState error={limits.error} what="the limit table" />}
        {limits.data && (
          <dl className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
            {Object.entries(limits.data.limits).map(([key, value]) => (
              <div
                key={key}
                className="border-line/50 flex items-baseline justify-between gap-3 border-b pb-1.5 text-xs"
              >
                <dt className="text-muted truncate">{key.replace(/_/g, ' ')}</dt>
                <dd className="num shrink-0">{String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </Panel>

      <Panel title="The rule table" asOf={limits.data?.as_of ?? null}>
        <p className="text-muted mb-4 text-xs leading-relaxed">
          Read straight off the Kernel rather than kept as a copy, so this cannot drift from the
          rules actually in force. Every one is evaluated on every proposal — passes included —
          because the ledger has to show every reason a trade died.
        </p>
        {limits.data && (
          <DataTable columns={RULE_COLUMNS} rows={limits.data.rules} rowKey={(r) => r.rule_id} />
        )}
      </Panel>
    </>
  )
}

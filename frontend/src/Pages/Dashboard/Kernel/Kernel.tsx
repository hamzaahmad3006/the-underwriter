import { Badge } from '../../../components/ui/Badge'
import { Button } from '../../../components/ui/Button'
import { DataTable } from '../../../components/ui/DataTable'
import { EmptyState } from '../../../components/ui/EmptyState'
import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { Stat } from '../../../components/ui/Stat'
import type { RuleResult, VetoMetrics } from '../../../api/types'
import { CATASTROPHIC_PROPOSAL, SOUND_PROPOSAL, useKernel } from './useKernel'

const RULE_COLUMNS = [
  {
    key: 'rule',
    header: 'Rule',
    render: (rule: RuleResult) => (
      <span className="num text-xs">
        {rule.rule_id} <span className="text-muted">{rule.name}</span>
      </span>
    ),
  },
  {
    key: 'outcome',
    header: 'Outcome',
    render: (rule: RuleResult) =>
      rule.passed ? (
        <Badge tone="positive">PASS</Badge>
      ) : rule.severity === 'HARD' ? (
        <Badge tone="critical">VETO</Badge>
      ) : (
        <Badge tone="caution">SOFT</Badge>
      ),
  },
  { key: 'observed', header: 'Observed', numeric: true, render: (r: RuleResult) => r.observed },
  { key: 'limit', header: 'Limit', numeric: true, render: (r: RuleResult) => r.limit },
]


/**
 * OPS-008 — the per-rule veto breakdown.
 *
 * The SRS calls this "both an operational signal and a judging artifact", and
 * the second half is why it gets a panel rather than a line in a metrics dump.
 * "The Kernel vetoed 17 of 31 proposals, and SK-006 accounts for 9 of them" is
 * the shortest true statement of what this system does.
 *
 * Bars are scaled to the busiest rule rather than to the proposal count, so the
 * shape stays readable when one rule dominates — which is the normal case.
 */
function VetoBreakdown({ data }: { data: VetoMetrics }) {
  const busiest = Math.max(...data.by_rule.map((rule) => rule.failures), 1)

  return (
    <div className="space-y-5">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4">
        <Stat label="Proposals adjudicated" value={String(data.proposals_evaluated)} />
        <Stat label="Approved" value={String(data.approved)} tone="positive" />
        <Stat label="Vetoed" value={String(data.vetoed)} tone="critical" />
        <Stat
          label="Veto rate"
          value={data.veto_rate === null ? '—' : `${Math.round(data.veto_rate * 100)}%`}
          tone={data.veto_rate === null ? 'muted' : 'default'}
          hint={`${data.rules_exercised} rules exercised`}
        />
      </dl>

      {data.by_rule.length === 0 ? (
        <EmptyState
          title="No rule has vetoed anything yet"
          reason={
            data.proposals_evaluated === 0
              ? 'The Kernel has not adjudicated a proposal since this book was opened.'
              : 'Every proposal so far has passed all twenty-six rules.'
          }
          hint="Run the catastrophic proposal above to see the same counters move."
        />
      ) : (
        <ul className="space-y-2.5">
          {data.by_rule.map((rule) => (
            <li key={rule.rule_id}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <span className="num truncate">
                  {rule.rule_id}
                  {rule.name && <span className="text-muted ml-2">{rule.name}</span>}
                </span>
                <span className="num text-muted shrink-0 tabular-nums">
                  {rule.failures}
                  {rule.proposals_blocked !== rule.failures && (
                    <span className="text-faint"> / {rule.proposals_blocked} blocked</span>
                  )}
                </span>
              </div>
              <div className="bg-line mt-1 h-1.5 overflow-hidden rounded-full">
                <div
                  className={rule.severity === 'SOFT' ? 'bg-caution h-full' : 'bg-critical h-full'}
                  style={{ width: `${Math.max((rule.failures / busiest) * 100, 4)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * §21.5 — the signature panel.
 *
 * The simulator posts to the real Kernel and shows every rule it evaluated,
 * passes included. Watching a deliberately catastrophic proposal die against
 * four named rules at once is the demo.
 */
export function Kernel() {
  const { limits, vetoes, simulation } = useKernel()
  const result = simulation.data

  return (
    <>
      <Panel
        title="Kernel simulator — API-045"
        asOf={result?.as_of ?? null}
        actions={
          <div className="flex gap-2">
            <Button
              variant="secondary"
              disabled={simulation.isPending}
              onClick={() => simulation.mutate(SOUND_PROPOSAL)}
            >
              Sound proposal
            </Button>
            <Button
              variant="danger"
              disabled={simulation.isPending}
              onClick={() => simulation.mutate(CATASTROPHIC_PROPOSAL)}
            >
              Catastrophic proposal
            </Button>
          </div>
        }
      >
        <p className="text-muted text-xs leading-relaxed">
          This runs the real Kernel — the same <code className="num">evaluate()</code> the scheduler
          calls. Nothing is transmitted: this endpoint has no execution engine behind it at all,
          which is a stronger guarantee than a flag.
        </p>

        {simulation.isPending && (
          <div className="mt-4">
            <Skeleton rows={4} />
          </div>
        )}

        {simulation.error && (
          <div className="mt-4">
            <ErrorState error={simulation.error} what="the simulation" />
          </div>
        )}

        {result && (
          <div className="mt-4 space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={result.verdict.verdict === 'APPROVE' ? 'positive' : 'critical'}>
                {result.verdict.verdict}
              </Badge>
              <span className="num text-sm">
                {result.verdict.approved_contracts} contract
                {result.verdict.approved_contracts === 1 ? '' : 's'} approved
              </span>
              <Badge tone="neutral">executed: {String(result.executed)}</Badge>
              <span className="text-faint num text-[11px]">
                {result.verdict.rules_failed} of {result.verdict.rules_evaluated} rules failed
              </span>
            </div>

            {result.verdict.reject_reasons.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {result.verdict.reject_reasons.map((reason) => (
                  <Badge key={reason} tone="critical">
                    {reason}
                  </Badge>
                ))}
              </div>
            )}

            <DataTable
              columns={RULE_COLUMNS}
              rows={result.verdict.rules}
              rowKey={(rule) => rule.rule_id}
            />
          </div>
        )}
      </Panel>

      <Panel title="What actually stops trades — API-078" asOf={vetoes.data?.as_of ?? null}>
        <p className="text-muted mb-4 text-xs leading-relaxed">
          Counted from the stored <code className="num">risk_checks</code> rows rather than from a
          tally held in memory, so a restart loses nothing and these numbers cannot disagree with
          the ledger behind them.
        </p>
        {vetoes.isLoading && <Skeleton rows={4} />}
        {vetoes.error && <ErrorState error={vetoes.error} what="the veto breakdown" />}
        {vetoes.data && <VetoBreakdown data={vetoes.data} />}
      </Panel>

      <Panel title="Active rule table — API-041" asOf={limits.data?.as_of ?? null}>
        {limits.isLoading && <Skeleton rows={5} />}
        {limits.error && <ErrorState error={limits.error} what="the rule table" />}
        {limits.data && (
          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {Object.entries(limits.data.limits).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4 text-xs">
                <dt className="text-muted">{key}</dt>
                <dd className="num">{String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </Panel>
    </>
  )
}

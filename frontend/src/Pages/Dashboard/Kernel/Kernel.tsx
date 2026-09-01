import { Badge } from '../../../components/ui/Badge'
import { Button } from '../../../components/ui/Button'
import { DataTable } from '../../../components/ui/DataTable'
import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import type { RuleResult } from '../../../api/types'
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
 * §21.5 — the signature panel.
 *
 * The simulator posts to the real Kernel and shows every rule it evaluated,
 * passes included. Watching a deliberately catastrophic proposal die against
 * four named rules at once is the demo.
 */
export function Kernel() {
  const { limits, simulation } = useKernel()
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

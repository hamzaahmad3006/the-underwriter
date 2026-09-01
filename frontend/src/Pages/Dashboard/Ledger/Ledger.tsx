import type { DecisionRow, VerdictRow } from '../../../api/types'
import { Badge } from '../../../components/ui/Badge'
import { type Column, DataTable } from '../../../components/ui/DataTable'
import { EmptyState } from '../../../components/ui/EmptyState'
import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { useLedger } from './useLedger'

const DECISION_COLUMNS: Column<DecisionRow>[] = [
  {
    key: 'when',
    header: 'When',
    render: (r) => (
      <span className="num text-muted">{new Date(r.created_at).toLocaleString()}</span>
    ),
  },
  {
    key: 'action',
    header: 'Action',
    render: (r) =>
      r.action === 'WRITE' ? (
        <Badge tone="positive">WRITE</Badge>
      ) : (
        <Badge tone="neutral">DECLINE</Badge>
      ),
  },
  { key: 'confidence', header: 'Confidence', numeric: true, render: (r) => r.confidence ?? '—' },
  {
    key: 'rationale',
    header: 'Rationale',
    render: (r) => (
      <span className="text-muted">{r.rationale ?? r.declined_reason ?? '—'}</span>
    ),
  },
  {
    key: 'prompt',
    header: 'Prompt',
    render: (r) => (
      <span className="num text-faint" title="SHA-256 of the versioned system prompt (FR-045)">
        {r.prompt_sha256 ? `${r.prompt_sha256.slice(0, 10)}…` : '—'}
      </span>
    ),
  },
  {
    key: 'cost',
    header: 'Tokens / latency',
    numeric: true,
    render: (r) => `${r.prompt_tokens ?? 0}/${r.completion_tokens ?? 0} · ${r.latency_ms ?? 0}ms`,
  },
]

const VERDICT_COLUMNS: Column<VerdictRow>[] = [
  {
    key: 'when',
    header: 'Issued',
    render: (r) => (
      <span className="num text-muted">{new Date(r.issued_at).toLocaleString()}</span>
    ),
  },
  {
    key: 'verdict',
    header: 'Verdict',
    render: (r) =>
      r.verdict === 'APPROVE' ? (
        <Badge tone="positive">APPROVE</Badge>
      ) : (
        <Badge tone="critical">VETO</Badge>
      ),
  },
  {
    key: 'contracts',
    header: 'Approved',
    numeric: true,
    render: (r) => r.approved_contracts,
  },
  {
    key: 'reasons',
    header: 'Reasons',
    render: (r) =>
      r.reject_reasons.length === 0 ? (
        <span className="text-faint">—</span>
      ) : (
        <span className="flex flex-wrap gap-1">
          {r.reject_reasons.map((reason) => (
            <Badge key={reason} tone="critical">
              {reason}
            </Badge>
          ))}
        </span>
      ),
  },
  {
    key: 'hash',
    header: 'Proposal',
    render: (r) => <span className="num text-faint">{r.proposal_hash.slice(0, 10)}…</span>,
  },
]

/**
 * §21.4 — every model decision with its rationale, and the verdict that
 * followed it.
 *
 * The prompt hash is what makes a decision reproducible six weeks later: same
 * prompt, same model, same snapshot, same answer.
 */
export function Ledger() {
  const { decisions, kernelDecisions } = useLedger()
  const feed = kernelDecisions.data

  return (
    <>
      <Panel
        title="Kernel verdicts — API-043"
        asOf={feed?.as_of ?? null}
        actions={
          feed && !feed.empty ? (
            <span className="text-faint num text-[11px]">
              {feed.vetoed} vetoed / {feed.approved + feed.vetoed}
            </span>
          ) : undefined
        }
      >
        <p className="text-muted mb-4 text-xs leading-relaxed">
          The Veto Feed. Rejections matter more than approvals here — watching the Kernel refuse
          things is the product. Every verdict lists the rules that failed, and the signature is
          never shown: an endpoint that returns one is an endpoint that can leak one.
        </p>

        {kernelDecisions.isLoading && <Skeleton rows={4} />}
        {kernelDecisions.error && <ErrorState error={kernelDecisions.error} what="the veto feed" />}
        {feed && (
          <DataTable
            columns={VERDICT_COLUMNS}
            rows={feed.decisions}
            rowKey={(r) => r.id}
            empty={
              <EmptyState
                title="No verdicts yet"
                reason="The Kernel has not adjudicated a proposal in this database."
                hint="The Kernel page runs the same rule table live — try a catastrophic proposal there."
              />
            }
          />
        )}
      </Panel>

      <Panel title="Underwriting decisions — API-031" asOf={decisions.data?.as_of ?? null}>
        <p className="text-muted mb-4 text-xs leading-relaxed">
          One model call per cycle, temperature 0.2, structured output enforced. Each row keeps the
          exact prompt hash, model version, token counts and latency that produced it — a decision
          nobody can reproduce is a decision nobody can audit.
        </p>

        {decisions.isLoading && <Skeleton rows={4} />}
        {decisions.error && <ErrorState error={decisions.error} what="the decision ledger" />}
        {decisions.data && (
          <DataTable
            columns={DECISION_COLUMNS}
            rows={decisions.data.decisions}
            rowKey={(r) => r.id}
            empty={
              <EmptyState
                title="No decisions yet"
                reason="The AI Underwriter has not been asked for a decision in this database."
                hint="Decisions appear here after the first underwriting cycle runs."
              />
            }
          />
        )}
      </Panel>
    </>
  )
}

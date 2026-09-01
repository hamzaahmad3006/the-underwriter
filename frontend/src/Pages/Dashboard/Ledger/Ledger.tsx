import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { useLedger } from './useLedger'

export function Ledger() {
  const { decisions } = useLedger()

  return (
    <Panel title="Decision ledger — API-031" asOf={null}>
      <p className="text-muted mb-4 text-xs leading-relaxed">Every LLM decision with its rationale, the prompt hash, and the verdict that followed (§21.4).</p>
      {decisions.isLoading && <Skeleton rows={4} />}
      {decisions.error && <ErrorState error={decisions.error} what="the decision ledger" />}
      {decisions.data != null && (
        <pre className="num text-muted overflow-x-auto text-[11px]">
          {JSON.stringify(decisions.data, null, 2)}
        </pre>
      )}
    </Panel>
  )
}

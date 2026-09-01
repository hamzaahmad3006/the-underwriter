import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { useBook } from './useBook'

export function Book() {
  const { policies } = useBook()

  return (
    <Panel title="Underwriting book — API-020" asOf={null}>
      <p className="text-muted mb-4 text-xs leading-relaxed">Every open and settled policy, with the verdict that authorised it (§21.2).</p>
      {policies.isLoading && <Skeleton rows={4} />}
      {policies.error && <ErrorState error={policies.error} what="the underwriting book" />}
      {policies.data != null && (
        <pre className="num text-muted overflow-x-auto text-[11px]">
          {JSON.stringify(policies.data, null, 2)}
        </pre>
      )}
    </Panel>
  )
}

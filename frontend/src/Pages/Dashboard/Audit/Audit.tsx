import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { useAudit } from './useAudit'

export function Audit() {
  const { log } = useAudit()

  return (
    <Panel title="Audit trail — API-060" asOf={null}>
      <p className="text-muted mb-4 text-xs leading-relaxed">The hash-chained record. API-061 walks the chain and reports the first break, if any.</p>
      {log.isLoading && <Skeleton rows={4} />}
      {log.error && <ErrorState error={log.error} what="the audit trail" />}
      {log.data != null && (
        <pre className="num text-muted overflow-x-auto text-[11px]">
          {JSON.stringify(log.data, null, 2)}
        </pre>
      )}
    </Panel>
  )
}

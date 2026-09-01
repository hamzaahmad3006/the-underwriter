import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { useRisk } from './useRisk'

export function Risk() {
  const { exposure } = useRisk()

  return (
    <Panel title="Risk centre — API-040" asOf={null}>
      <p className="text-muted mb-4 text-xs leading-relaxed">Greeks, concentration, reserve utilisation and headroom against every limit (§21.3).</p>
      {exposure.isLoading && <Skeleton rows={4} />}
      {exposure.error && <ErrorState error={exposure.error} what="portfolio exposure" />}
      {exposure.data != null && (
        <pre className="num text-muted overflow-x-auto text-[11px]">
          {JSON.stringify(exposure.data, null, 2)}
        </pre>
      )}
    </Panel>
  )
}

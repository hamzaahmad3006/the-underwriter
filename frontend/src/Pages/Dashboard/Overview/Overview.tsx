import { ErrorState } from '../../../components/ui/ErrorState'
import { Panel } from '../../../components/ui/Panel'
import { Skeleton } from '../../../components/ui/Skeleton'
import { useOverview } from './useOverview'

export function Overview() {
  const { overview } = useOverview()

  return (
    <Panel title="Executive overview — API-010" asOf={null}>
      <p className="text-muted mb-4 text-xs leading-relaxed">Capital, P&L, book shape and the Kernel's veto rate — the tiles a judge reads first (§21.1).</p>
      {overview.isLoading && <Skeleton rows={4} />}
      {overview.error && <ErrorState error={overview.error} what="the executive overview" />}
      {overview.data != null && (
        <pre className="num text-muted overflow-x-auto text-[11px]">
          {JSON.stringify(overview.data, null, 2)}
        </pre>
      )}
    </Panel>
  )
}

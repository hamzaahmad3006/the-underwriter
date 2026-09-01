import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { endpoints } from '../api/endpoints'
import type { SystemStatus } from '../api/types'
import { Badge } from './ui/Badge'

/**
 * Mode and kill switch, always visible.
 *
 * A halted desk that looks identical to a running one is the single most
 * misleading thing this dashboard could do, so the state lives in the header
 * rather than on a page someone has to navigate to.
 */
export function SystemBadge() {
  const { data, isError } = useQuery({
    queryKey: ['system', 'status'],
    queryFn: () => api.get<SystemStatus>(endpoints.status),
  })

  if (isError) return <Badge tone="critical">UNREACHABLE</Badge>
  if (!data) return <Badge tone="neutral">···</Badge>

  if (data.kill_switch) return <Badge tone="critical">KILL SWITCH</Badge>

  const tone = data.mode === 'ACTIVE' ? 'positive' : data.mode === 'HALT' ? 'critical' : 'caution'
  return (
    <div className="flex items-center gap-1.5">
      <Badge tone={tone}>{data.mode}</Badge>
      <Badge tone="neutral">PAPER</Badge>
    </div>
  )
}

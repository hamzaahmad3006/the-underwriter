import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'
import type { EffectiveConfig, LimitTable, Overview, SystemStatus } from '../../../api/types'
import type { EquityPoint } from './EquityCurve'

export interface EquityCurveResponse {
  as_of: string
  baseline_equity: string | null
  points: EquityPoint[]
  empty: boolean
}

/**
 * §21.1 — the tiles a judge reads first.
 *
 * Capital and P&L come from the book, which needs the persistence layer to
 * have run at least one cycle. System state and the active limits are live
 * now, so the page shows what it genuinely knows and explains the rest
 * (UI-006) rather than filling the space with plausible numbers.
 */
export function useOverview() {
  const status = useQuery({
    queryKey: ['system', 'status'],
    queryFn: () => api.get<SystemStatus>(endpoints.status),
  })

  const config = useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<EffectiveConfig>(endpoints.config),
  })

  const limits = useQuery({
    queryKey: ['risk', 'limits'],
    queryFn: () => api.get<LimitTable>(endpoints.limits),
  })

  const overview = useQuery({
    queryKey: ['dashboard', 'overview'],
    queryFn: () => api.get<Overview>(endpoints.overview),
    retry: false,
  })

  const curve = useQuery({
    queryKey: ['dashboard', 'equity-curve'],
    queryFn: () => api.get<EquityCurveResponse>(endpoints.equityCurve('all')),
    retry: false,
  })

  return { status, config, limits, overview, curve }
}

import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'
import type { Exposure, LimitTable } from '../../../api/types'

/**
 * §21.3 — Greeks, concentration, reserve utilisation, and headroom on every
 * limit. The rule table is live and comes straight off the Kernel, so the
 * dashboard can never drift from the rules actually in force (TD-10).
 */
export function useRisk() {
  const limits = useQuery({
    queryKey: ['risk', 'limits'],
    queryFn: () => api.get<LimitTable>(endpoints.limits),
  })

  const exposure = useQuery({
    queryKey: ['risk', 'exposure'],
    queryFn: () => api.get<Exposure>(endpoints.exposure),
    retry: false,
  })

  const events = useQuery({
    queryKey: ['risk', 'events'],
    queryFn: () => api.get<unknown>(endpoints.riskEvents),
    retry: false,
  })

  return { limits, exposure, events }
}

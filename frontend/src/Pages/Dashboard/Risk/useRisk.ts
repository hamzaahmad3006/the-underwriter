import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'

/**
 * Greeks, concentration, reserve utilisation and headroom against every limit (§21.3).
 *
 * The panels below are wired to real endpoints. Those endpoints answer 503
 * with a reason until the persistence layer lands, and the UI renders that
 * reason (UI-006) rather than inventing numbers to fill the space.
 */
export function useRisk() {
  const exposure = useQuery({
    queryKey: ['risk', 'exposure'],
    queryFn: () => api.get<unknown>(endpoints.exposure),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })
  const events = useQuery({
    queryKey: ['risk', 'events'],
    queryFn: () => api.get<unknown>(endpoints.riskEvents),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })

  return {
    exposure,
    events,
  }
}

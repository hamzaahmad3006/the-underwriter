import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'

/**
 * Capital, P&L, book shape and the Kernel's veto rate — the tiles a judge reads first (§21.1).
 *
 * The panels below are wired to real endpoints. Those endpoints answer 503
 * with a reason until the persistence layer lands, and the UI renders that
 * reason (UI-006) rather than inventing numbers to fill the space.
 */
export function useOverview() {
  const overview = useQuery({
    queryKey: ['overview', 'overview'],
    queryFn: () => api.get<unknown>(endpoints.overview),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })
  const stats = useQuery({
    queryKey: ['overview', 'stats'],
    queryFn: () => api.get<unknown>(endpoints.stats),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })

  return {
    overview,
    stats,
  }
}

import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'

/**
 * Every open and settled policy, with the verdict that authorised it (§21.2).
 *
 * The panels below are wired to real endpoints. Those endpoints answer 503
 * with a reason until the persistence layer lands, and the UI renders that
 * reason (UI-006) rather than inventing numbers to fill the space.
 */
export function useBook() {
  const policies = useQuery({
    queryKey: ['book', 'policies'],
    queryFn: () => api.get<unknown>(endpoints.policies),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })

  return {
    policies,
  }
}

import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'

/**
 * Every LLM decision with its rationale, the prompt hash, and the verdict that followed (§21.4).
 *
 * The panels below are wired to real endpoints. Those endpoints answer 503
 * with a reason until the persistence layer lands, and the UI renders that
 * reason (UI-006) rather than inventing numbers to fill the space.
 */
export function useLedger() {
  const decisions = useQuery({
    queryKey: ['ledger', 'decisions'],
    queryFn: () => api.get<unknown>(endpoints.decisions),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })

  return {
    decisions,
  }
}

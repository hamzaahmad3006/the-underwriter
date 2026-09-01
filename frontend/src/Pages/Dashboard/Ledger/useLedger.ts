import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'
import type { DecisionList, VerdictFeed } from '../../../api/types'

/**
 * §21.4 — the decision ledger and the verdicts that followed it.
 *
 * Both need the persistence layer to have recorded a cycle. Until then they
 * answer 503 with the reason, and the UI renders that reason (UI-006) rather
 * than filling the page with plausible-looking decisions.
 */
export function useLedger() {
  const decisions = useQuery({
    queryKey: ['underwriting', 'decisions'],
    queryFn: () => api.get<DecisionList>(endpoints.decisions),
    retry: false,
  })

  const kernelDecisions = useQuery({
    queryKey: ['kernel', 'decisions'],
    queryFn: () => api.get<VerdictFeed>(endpoints.kernelDecisions),
    retry: false,
  })

  return { decisions, kernelDecisions }
}

import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'
import type { PolicyList } from '../../../api/types'

export interface CandidateRow {
  candidate_id: string
  underlying: string
  expiry: string
  dte: number
  short_strike: string
  long_strike: string
  net_credit: string
  max_loss: string
  edge_ratio: string
  liquidity_score: string
  short_delta: string
}

export interface DiscardRow {
  candidate_id: string
  reason: string
  detail: string
}

export interface CandidateSet {
  as_of: string
  aborted: string | null
  detail: string
  candidates: CandidateRow[]
  discards: DiscardRow[]
  contracts_seen?: number
}

/**
 * §21.2 — the book, and what the Actuary is looking at right now.
 *
 * The candidate set is live: it runs the real data layer and the real Actuary.
 * The discards come back with it, because an empty book has to explain itself
 * and "nothing qualified" is a result rather than a failure (FR-026).
 */
export function useBook() {
  const policies = useQuery({
    queryKey: ['policies'],
    queryFn: () => api.get<PolicyList>(endpoints.policies),
    retry: false,
  })

  const candidates = useQuery({
    queryKey: ['underwriting', 'candidates'],
    queryFn: () => api.get<CandidateSet>(endpoints.candidates),
    retry: false,
    // The chain fetch is slow and rate-limited; this does not need 10s polling.
    refetchInterval: 60_000,
  })

  return { policies, candidates }
}

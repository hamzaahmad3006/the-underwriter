import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'
import type {
  KernelVerdict,
  LimitTable,
  SimulateBody,
  SimulationResult,
  VetoMetrics,
} from '../../../api/types'

/** A proposal the Kernel should wave through. */
export const SOUND_PROPOSAL: SimulateBody = {
  underlying: 'SPY',
  short_strike: '550',
  long_strike: '548',
  dte: 17,
  max_loss: '150.00',
  requested_contracts: 2,
  edge_ratio: '0.10',
  liquidity_score: '0.80',
  greeks_complete: true,
  naked: false,
  candidate_is_known: true,
}

/**
 * The demo proposal: 90% of NAV at risk, no covering leg, expiring today, and
 * an instrument that was never offered to the model. Every one of those is a
 * separate HARD rule, and the point is that they are all reported at once.
 */
export const CATASTROPHIC_PROPOSAL: SimulateBody = {
  ...SOUND_PROPOSAL,
  max_loss: '90000.00',
  dte: 0,
  requested_contracts: 50,
  naked: true,
  candidate_is_known: false,
}

export function useKernel() {
  const limits = useQuery({
    queryKey: ['risk', 'limits'],
    queryFn: () => api.get<LimitTable>(endpoints.limits),
  })

  const feed = useQuery({
    queryKey: ['kernel', 'decisions'],
    queryFn: () => api.get<{ decisions: KernelVerdict[] }>(endpoints.kernelDecisions),
    retry: false, // a 503 here is a known gap, not a flake
  })

  // OPS-008 requires these to be queryable *and* displayed. Polled with
  // everything else rather than fetched once: a veto that lands while the page
  // is open is exactly the moment the number is worth watching.
  const vetoes = useQuery({
    queryKey: ['kernel', 'vetoes'],
    queryFn: () => api.get<VetoMetrics>(endpoints.vetoMetrics),
  })

  const simulation = useMutation({
    mutationFn: (body: SimulateBody) =>
      api.post<SimulationResult>(endpoints.kernelSimulate, body),
  })

  return { limits, feed, vetoes, simulation }
}

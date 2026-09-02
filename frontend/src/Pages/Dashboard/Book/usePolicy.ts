import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'
import type { PolicyRow } from '../../../api/types'

export interface LegRow {
  symbol: string
  side: string
  position_intent: string | null
  ratio_qty: number
  strike: string | null
  expiration: string | null
  option_type: string | null
  open_price: string | null
  close_price: string | null
  open_delta: string | null
  open_iv: string | null
}

export interface OrderRow {
  id: string
  client_order_id: string
  alpaca_order_id: string | null
  /** NFR-008: never null. The column is NOT NULL in the schema. */
  kernel_decision_id: string
  intent: string
  status: string
  limit_price: string | null
  filled_qty: string | null
  filled_avg_price: string | null
  submitted_at: string | null
}

export interface PolicyDetailResponse {
  as_of: string
  policy: PolicyRow & { predicted_confidence: string | null }
  legs: LegRow[]
  orders: OrderRow[]
  fills: { symbol: string; side: string; qty: string | null; price: string | null }[]
  lifecycle: string[]
}

/** UI-004: `/dashboard/book/:policyId` is deep-linkable, so it fetches its own. */
export function usePolicy(policyId: string) {
  return useQuery({
    queryKey: ['policy', policyId],
    queryFn: () => api.get<PolicyDetailResponse>(endpoints.policy(policyId)),
    enabled: Boolean(policyId),
    retry: false,
  })
}

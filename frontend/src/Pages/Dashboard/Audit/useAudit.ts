import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'

export interface AuditRecord {
  seq: number
  occurred_at: string
  correlation_id: string | null
  actor: string
  action: string
  entity_type: string | null
  entity_id: string | null
  prev_hash: string | null
  record_hash: string
}

export interface ChainVerification {
  as_of: string
  valid: boolean
  records_checked: number
  first_break_seq: number | null
  detail: string
}

export interface AuditTrail {
  as_of: string
  records: AuditRecord[]
  returned: number
  highest_seq: number
}

/**
 * The audit trail and its hash chain. Both are live.
 *
 * A break answers 200 with `valid: false` rather than an error, so the UI has
 * to render the finding rather than an error state — hiding it behind a 500
 * would defeat the endpoint.
 */
export function useAudit() {
  const verification = useQuery({
    queryKey: ['audit', 'verify'],
    queryFn: () => api.get<ChainVerification>(endpoints.auditVerify),
    retry: false,
  })

  const log = useQuery({
    queryKey: ['audit', 'log'],
    queryFn: () => api.get<AuditTrail>(endpoints.auditLog),
    retry: false,
  })

  return { verification, log }
}

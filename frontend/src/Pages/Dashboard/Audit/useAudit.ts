import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'

/**
 * The hash-chained record. API-061 walks the chain and reports the first break, if any.
 *
 * The panels below are wired to real endpoints. Those endpoints answer 503
 * with a reason until the persistence layer lands, and the UI renders that
 * reason (UI-006) rather than inventing numbers to fill the space.
 */
export function useAudit() {
  const log = useQuery({
    queryKey: ['audit', 'log'],
    queryFn: () => api.get<unknown>(endpoints.auditLog),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })
  const verify = useQuery({
    queryKey: ['audit', 'verify'],
    queryFn: () => api.get<unknown>(endpoints.auditVerify),
    retry: false, // a 503 from an unbuilt endpoint is a known gap, not a flake
  })

  return {
    log,
    verify,
  }
}

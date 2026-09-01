import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { endpoints } from '../../../api/endpoints'
import type { EffectiveConfig, SystemStatus } from '../../../api/types'

/**
 * The landing page shows live system state rather than a marketing claim.
 * If the desk is halted, the first thing a visitor sees should say so.
 */
export function useLanding() {
  const status = useQuery({
    queryKey: ['system', 'status'],
    queryFn: () => api.get<SystemStatus>(endpoints.status),
  })

  const config = useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<EffectiveConfig>(endpoints.config),
  })

  return {
    status: status.data ?? null,
    config: config.data ?? null,
    isLoading: status.isLoading || config.isLoading,
    error: status.error ?? config.error,
  }
}

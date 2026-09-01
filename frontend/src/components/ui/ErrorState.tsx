import { ApiRequestError } from '../../api/client'
import { EmptyState } from './EmptyState'

/**
 * UI-005: errors are inline and specific.
 *
 * A 503 from an endpoint whose data layer is not built is not a failure to
 * apologise for — it is a known gap, so it renders as an explained empty state
 * rather than as an error.
 */
export function ErrorState({ error, what }: { error: unknown; what: string }) {
  if (error instanceof ApiRequestError && error.isNotReady) {
    return <EmptyState title={`${what} is not live yet`} reason={error.blockedOn ?? error.message} />
  }

  const message = error instanceof Error ? error.message : String(error)
  const correlationId = error instanceof ApiRequestError ? error.correlationId : null

  return (
    <div className="border-critical/40 bg-critical/5 rounded border px-4 py-3">
      <p className="text-critical text-sm font-medium">Could not load {what}</p>
      <p className="text-muted mt-1 text-xs">{message}</p>
      {correlationId && <p className="text-faint num mt-2 text-[11px]">{correlationId}</p>}
    </div>
  )
}

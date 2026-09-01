/**
 * UI-005: loading states are skeletons, never a spinner over stale numbers.
 * A spinner on top of last minute's P&L is a lie that looks like a feature.
 */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="bg-raised h-4 animate-pulse rounded" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  )
}

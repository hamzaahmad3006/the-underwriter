/**
 * UI-006: an empty panel must explain *why* it is empty, never say "No data".
 * "No policies open — the Kernel vetoed the last 3 proposals" is information;
 * "No data" is an apology.
 */
export function EmptyState({ title, reason, hint }: { title: string; reason: string; hint?: string }) {
  return (
    <div className="border-line rounded border border-dashed px-4 py-6 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="text-muted mt-1 text-xs">{reason}</p>
      {hint && <p className="text-faint mt-2 text-xs">{hint}</p>}
    </div>
  )
}

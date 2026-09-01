/**
 * One number with its label. UI-007: money is right-aligned with tabular-nums,
 * and a loss uses the semantic negative token rather than bare red.
 */
export function Stat({
  label,
  value,
  tone = 'default',
  hint,
}: {
  label: string
  value: string
  tone?: 'default' | 'positive' | 'critical' | 'muted'
  hint?: string
}) {
  const toneClass = {
    default: 'text-ink',
    positive: 'text-positive',
    critical: 'text-critical',
    muted: 'text-muted',
  }[tone]

  return (
    <div>
      <dt className="text-muted text-[11px] tracking-wide uppercase">{label}</dt>
      <dd className={`num mt-0.5 text-lg ${toneClass}`}>{value}</dd>
      {hint && <p className="text-faint mt-0.5 text-[11px]">{hint}</p>}
    </div>
  )
}

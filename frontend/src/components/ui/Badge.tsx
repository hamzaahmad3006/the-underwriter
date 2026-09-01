type Tone = 'positive' | 'caution' | 'critical' | 'neutral'

const TONES: Record<Tone, string> = {
  positive: 'border-positive/40 text-positive',
  caution: 'border-caution/40 text-caution',
  critical: 'border-critical/40 text-critical',
  neutral: 'border-line text-muted',
}

/**
 * UI-009: colour is never the sole carrier of meaning, so a badge always
 * shows a label too. Someone who cannot distinguish the greens from the reds
 * still reads "VETOED".
 */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${TONES[tone]}`}>
      {children}
    </span>
  )
}

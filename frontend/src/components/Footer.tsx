/**
 * UI-012: the honesty statement (§15.7) appears in the footer of every page.
 * It is normative text, so it is a constant here rather than copy someone can
 * soften later.
 */
export const HONESTY_STATEMENT =
  'Credit spreads win often and lose occasionally by a larger amount. Four sessions is not a ' +
  'statistically meaningful sample and no edge is claimed. What is guaranteed is a bounded, ' +
  'pre-computed maximum loss, enforced before any order is transmitted. Paper trading only.'

export function Footer() {
  return (
    <footer className="border-line text-faint mt-auto border-t px-6 py-3 text-[11px] leading-relaxed">
      {HONESTY_STATEMENT}
    </footer>
  )
}

import type { ReactNode } from 'react'

/**
 * The dashboard's only container.
 *
 * UI-002 is enforced structurally: `asOf` is a required prop, so a panel
 * cannot be rendered without stating when its numbers were true.
 */
export function Panel({
  title,
  asOf,
  actions,
  children,
}: {
  title: string
  asOf: string | null
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="border-line bg-panel rounded border">
      <header className="border-line flex items-baseline justify-between gap-4 border-b px-4 py-3">
        <h2 className="text-xs font-medium tracking-wider uppercase">{title}</h2>
        <div className="flex items-center gap-3">
          {actions}
          <time className="text-faint num text-[11px]">
            {asOf ? `as of ${new Date(asOf).toLocaleTimeString()}` : 'no data'}
          </time>
        </div>
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

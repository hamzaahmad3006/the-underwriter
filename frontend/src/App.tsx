// Placeholder shell. Real routing (UI-004) and panels (§21) land once the
// folder structure is fixed. This exists to prove the token layer renders.

const HONESTY =
  'Credit spreads win often and lose occasionally by a larger amount. Four sessions is not a ' +
  'statistically meaningful sample and no edge is claimed. What is guaranteed is a bounded, ' +
  'pre-computed maximum loss, enforced before any order is transmitted.'

export default function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line px-6 py-4">
        <h1 className="text-base font-semibold tracking-tight">THE UNDERWRITER</h1>
        <p className="text-muted text-xs">Autonomous options underwriting desk — paper trading only</p>
      </header>

      <main className="flex-1 p-6">
        <section className="border-line bg-panel max-w-md rounded border p-4">
          <h2 className="text-muted mb-3 text-xs font-medium tracking-wider uppercase">
            Boot check
          </h2>
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-muted">Tailwind tokens</dt>
            <dd className="text-positive num text-right">OK</dd>
            <dt className="text-muted">Numeric face</dt>
            <dd className="num text-right">1,234,567.89</dd>
            <dt className="text-muted">Backend</dt>
            <dd className="text-caution num text-right">not wired</dd>
          </dl>
        </section>
      </main>

      {/* UI-012: mandatory honesty statement, footer of every page (§15.7) */}
      <footer className="border-line text-faint border-t px-6 py-3 text-xs">{HONESTY}</footer>
    </div>
  )
}

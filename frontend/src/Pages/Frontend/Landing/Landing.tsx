import { Link } from 'react-router-dom'
import { Footer } from '../../../components/Footer'
import { Badge } from '../../../components/ui/Badge'
import { useLanding } from './useLanding'

const PIPELINE = [
  ['Market Data', 'Liquid option chains, validated. Missing Greeks are discarded, never estimated.'],
  ['Actuary', 'Deterministic Python prices every candidate. It computes; it never persuades.'],
  ['AI Underwriter', 'The LLM picks among pre-priced candidates and explains itself. It holds no credentials.'],
  ['Solvency Kernel', '25 deterministic rules, fail-closed, with veto authority. It cannot be argued with.'],
  ['Execution', 'Idempotent multi-leg orders — and only ever with a signed verdict.'],
  ['Claims Desk', 'Profit targets, stops, and mandatory flat before 0DTE.'],
]

/**
 * The first page the app opens on.
 *
 * It exists to make one claim legible in ten seconds, because that is roughly
 * how long a judge spends before deciding whether to keep reading.
 */
export function Landing() {
  const { status } = useLanding()

  return (
    <div className="flex min-h-screen flex-col">
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-16">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">THE UNDERWRITER</h1>
          {status && (
            <Badge tone={status.mode === 'ACTIVE' ? 'positive' : 'caution'}>{status.mode}</Badge>
          )}
          <Badge tone="neutral">PAPER</Badge>
        </div>

        <p className="text-muted mt-2 text-sm">An autonomous AI options underwriting desk.</p>

        <blockquote className="border-accent text-ink mt-10 border-l-2 pl-4 text-base leading-relaxed">
          The LLM proposes. The deterministic Solvency Kernel disposes.
          <br />
          <span className="text-muted">The LLM never holds execution authority.</span>
        </blockquote>

        <p className="text-muted mt-6 text-sm leading-relaxed">
          This is not a policy statement — it is enforced structurally. The AI Underwriter process
          has no Alpaca trading credentials. Execution requires a cryptographically signed verdict
          that only the Kernel can mint. A model that hallucinates, is prompt-injected, or is
          instructed by a hostile operator to liquidate the book cannot produce one, and therefore
          cannot trade.
        </p>

        <div className="mt-10 flex flex-wrap gap-3">
          <Link
            to="/dashboard"
            className="bg-accent/15 border-accent/50 text-accent hover:bg-accent/25 rounded border px-4 py-2 text-sm font-medium"
          >
            Open the desk
          </Link>
          <Link
            to="/dashboard/kernel"
            className="border-line text-muted hover:text-ink rounded border px-4 py-2 text-sm font-medium"
          >
            Try to break the Kernel
          </Link>
        </div>

        <ol className="mt-14 space-y-4">
          {PIPELINE.map(([name, description], index) => (
            <li key={name} className="flex gap-4">
              <span className="num text-faint pt-0.5 text-xs">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <h2 className="text-sm font-medium">{name}</h2>
                <p className="text-muted text-xs leading-relaxed">{description}</p>
              </div>
            </li>
          ))}
        </ol>
      </main>

      <Footer />
    </div>
  )
}

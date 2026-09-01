import { Link, Outlet } from 'react-router-dom'
import { Footer } from './Footer'
import { Nav } from './Nav'
import { OperatorTokenField } from './OperatorTokenField'

/**
 * The dashboard shell.
 *
 * UI-003 shapes it: everything below is readable with no token at all. The
 * token field gates controls, not visibility.
 */
export function Layout() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-line border-b px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link to="/" className="group">
            <h1 className="text-sm font-semibold tracking-tight">THE UNDERWRITER</h1>
            <p className="text-faint text-[11px]">Autonomous options underwriting desk</p>
          </Link>
          <OperatorTokenField />
        </div>
        <div className="mt-3">
          <Nav />
        </div>
      </header>

      <main className="flex-1 space-y-4 p-6">
        <Outlet />
      </main>

      <Footer />
    </div>
  )
}

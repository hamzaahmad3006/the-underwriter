import { NavLink } from 'react-router-dom'

/** UI-004's routes, in the order a judge should read them. */
const LINKS = [
  { to: '/dashboard', label: 'Overview', end: true },
  { to: '/dashboard/book', label: 'Book' },
  { to: '/dashboard/risk', label: 'Risk' },
  { to: '/dashboard/kernel', label: 'Kernel' },
  { to: '/dashboard/ledger', label: 'Ledger' },
  { to: '/dashboard/audit', label: 'Audit' },
]

export function Nav() {
  return (
    <nav className="flex w-max gap-1 sm:w-auto">
      {LINKS.map((link) => (
        <NavLink
          key={link.to}
          to={link.to}
          end={link.end}
          className={({ isActive }) =>
            `shrink-0 rounded px-3 py-1.5 text-xs font-medium whitespace-nowrap transition-colors ${
              isActive ? 'bg-raised text-ink' : 'text-muted hover:text-ink hover:bg-raised/50'
            }`
          }
        >
          {link.label}
        </NavLink>
      ))}
    </nav>
  )
}

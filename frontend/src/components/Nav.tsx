import { NavLink } from 'react-router-dom'

/** UI-004's deep-linkable routes, in the order a judge should read them. */
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
    <nav className="flex flex-wrap gap-1">
      {LINKS.map((link) => (
        <NavLink
          key={link.to}
          to={link.to}
          end={link.end}
          className={({ isActive }) =>
            `rounded px-2.5 py-1 text-xs font-medium transition-colors ${
              isActive ? 'bg-raised text-ink' : 'text-muted hover:text-ink'
            }`
          }
        >
          {link.label}
        </NavLink>
      ))}
    </nav>
  )
}

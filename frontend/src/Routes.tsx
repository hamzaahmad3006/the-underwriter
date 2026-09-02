/**
 * Every route in the app, in one file.
 *
 * `App.tsx` imports nothing but `<Routes />`, so this is the single place to
 * answer "what pages exist?".
 *
 * Named `.tsx`, not `.ts`, because it contains JSX — a `.ts` file cannot hold
 * a `<Route>` element.
 *
 * UI-004 requires deep-linkable routes. The dashboard's own paths live under
 * `/dashboard` so that `/` can be the landing page a first-time visitor lands
 * on; the SRS's original flat paths are recorded as moved in §20.
 */

import { BrowserRouter, Navigate, Route, Routes as RouterRoutes } from 'react-router-dom'

import { Layout } from './components/Layout'
import { Audit } from './Pages/Dashboard/Audit/Audit'
import { Book } from './Pages/Dashboard/Book/Book'
import { PolicyDetail } from './Pages/Dashboard/Book/PolicyDetail'
import { Kernel } from './Pages/Dashboard/Kernel/Kernel'
import { Ledger } from './Pages/Dashboard/Ledger/Ledger'
import { Overview } from './Pages/Dashboard/Overview/Overview'
import { Risk } from './Pages/Dashboard/Risk/Risk'
import { Landing } from './Pages/Frontend/Landing/Landing'

export function Routes() {
  return (
    <BrowserRouter>
      <RouterRoutes>
        {/* Frontend — the first page the app opens on */}
        <Route path="/" element={<Landing />} />

        {/* Dashboard — the desk itself */}
        <Route path="/dashboard" element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="book" element={<Book />} />
          <Route path="book/:policyId" element={<PolicyDetail />} />
          <Route path="risk" element={<Risk />} />
          <Route path="kernel" element={<Kernel />} />
          <Route path="ledger" element={<Ledger />} />
          <Route path="audit" element={<Audit />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </RouterRoutes>
    </BrowserRouter>
  )
}

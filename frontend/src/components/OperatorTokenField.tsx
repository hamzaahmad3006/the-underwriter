import { useState } from 'react'
import { getOperatorToken, setOperatorToken } from '../api/client'
import { Badge } from './ui/Badge'
import { Button } from './ui/Button'

/**
 * The whole of this system's "auth" (SEC-018).
 *
 * There is no login, no registration and no password reset: §31 puts OAuth and
 * multi-user accounts explicitly out of scope, and UI-003 requires the whole
 * dashboard to be readable without any token at all. One bearer token unlocks
 * the write controls, and SEC-012 means it still buys no privilege with the
 * Kernel — an operator's order is adjudicated by the same 25 rules.
 */
export function OperatorTokenField() {
  const [token, setToken] = useState(() => getOperatorToken() ?? '')
  const [saved, setSaved] = useState(() => Boolean(getOperatorToken()))

  return (
    <div className="flex items-center gap-2">
      <Badge tone={saved ? 'positive' : 'neutral'}>{saved ? 'OPERATOR' : 'READ-ONLY'}</Badge>
      <input
        type="password"
        value={token}
        onChange={(event) => {
          setToken(event.target.value)
          setSaved(false)
        }}
        placeholder="Operator token (optional)"
        aria-label="Operator token"
        className="border-line bg-surface focus:border-accent w-44 rounded border px-2 py-1 text-xs outline-none"
      />
      <Button
        onClick={() => {
          setOperatorToken(token || null)
          setSaved(Boolean(token))
        }}
      >
        {token ? 'Save' : 'Clear'}
      </Button>
    </div>
  )
}

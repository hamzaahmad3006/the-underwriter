/**
 * The only place that talks to the network.
 *
 * Two rules hold everywhere above this file:
 *   - a failure arrives as an `ApiRequestError` carrying the backend's error
 *     envelope, so a panel can render UI-005's specific inline message rather
 *     than "something went wrong";
 *   - the operator token is attached here and nowhere else.
 */

import type { ApiError } from './types'

const TOKEN_KEY = 'underwriter.operatorToken'

export class ApiRequestError extends Error {
  readonly status: number
  readonly code: string
  readonly blockedOn?: string
  readonly correlationId: string | null

  constructor(status: number, body: ApiError | null, fallback: string) {
    super(body?.error?.message ?? fallback)
    this.name = 'ApiRequestError'
    this.status = status
    this.code = body?.error?.code ?? 'UNKNOWN'
    this.blockedOn = body?.error?.blocked_on
    this.correlationId = body?.correlation_id ?? null
  }

  /** A specified endpoint whose data source is not built yet, not a bug. */
  get isNotReady(): boolean {
    return this.code === 'NOT_YET_IMPLEMENTED'
  }
}

export function getOperatorToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    // Private windows and blocked site data both throw. Read-only still works.
    return null
  }
}

export function setOperatorToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore: the dashboard is fully usable read-only (UI-003) */
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getOperatorToken()
  const headers: Record<string, string> = { Accept: 'application/json' }

  if (init?.body) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(path, { ...init, headers: { ...headers, ...init?.headers } })

  if (!response.ok) {
    let body: ApiError | null = null
    try {
      body = (await response.json()) as ApiError
    } catch {
      body = null
    }
    throw new ApiRequestError(response.status, body, `${response.status} ${response.statusText}`)
  }

  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
}

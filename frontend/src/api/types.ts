/**
 * Response shapes from the backend.
 *
 * Money arrives as a string, never a number. The backend computes every
 * monetary value in Decimal (NFR-013) and serialises it as a string
 * specifically so the browser's float arithmetic cannot round a reserve on the
 * way to the screen. Format for display; never do maths on these.
 */

export type Money = string
export type Ratio = string

export interface ApiError {
  error: {
    code: string
    message: string
    what?: string
    blocked_on?: string
    fields?: unknown[]
  }
  correlation_id: string | null
}

export interface SystemStatus {
  as_of: string
  version: string
  environment: string
  app_version: string
  uptime_sec: number
  mode: 'ACTIVE' | 'MANAGE_ONLY' | 'HALT'
  kill_switch: boolean
  strategy_profile: string
  paper_trading: string
  last_cycle: string | null
  note?: string
}

export interface EffectiveConfig {
  as_of: string
  strategy_profile: string
  kernel: Record<string, string | number>
  honesty_statement: string
}

export type Severity = 'HARD' | 'SOFT'

export interface RuleSpec {
  rule_id: string
  name: string
  severity: Severity
}

export interface LimitTable {
  as_of: string
  limits: Record<string, string | number>
  rules: RuleSpec[]
}

export interface RuleResult {
  rule_id: string
  name: string
  passed: boolean
  severity: Severity
  observed: string
  limit: string
  message: string
  reason_code: string | null
}

export interface KernelVerdict {
  verdict_id: string
  proposal_hash: string
  verdict: 'APPROVE' | 'REJECT'
  approved_contracts: number
  reject_reasons: string[]
  issued_at: string
  expires_at: string
  /** Whether a signature was minted. The signature itself is never sent. */
  signed: boolean
  rules: RuleResult[]
  rules_failed: number
  rules_evaluated: number
}

export interface SimulationResult {
  as_of: string
  /** Always false. This path has no execution engine behind it. */
  executed: false
  explanation: string
  verdict: KernelVerdict
}

export interface SimulateBody {
  underlying?: string
  action?: 'OPEN' | 'CLOSE'
  short_strike?: string
  long_strike?: string
  dte?: number
  requested_contracts?: number
  max_loss?: string
  edge_ratio?: string
  liquidity_score?: string
  greeks_complete?: boolean
  naked?: boolean
  candidate_is_known?: boolean
  nav?: string
  market_open?: boolean
  data_age_sec?: number
  mode?: 'ACTIVE' | 'MANAGE_ONLY' | 'HALT'
  kill_switch_engaged?: boolean
}

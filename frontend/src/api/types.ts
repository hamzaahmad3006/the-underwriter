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

// --- Live dashboard shapes -------------------------------------------------

export interface Overview {
  as_of: string
  capital: {
    baseline_equity: Money | null
    total_equity: Money | null
    available: Money | null
    reserved: Money
    at_risk_pct: Ratio
  }
  pnl: { realized: Money; unrealized: Money }
  book: { open_policies: number; closed_policies: number; policies_written: number }
  performance: {
    wins: number
    losses: number
    /** Null, not zero — with no settled policies there is no rate to report. */
    win_rate: Ratio | null
    loss_ratio: Ratio | null
    premiums_written: Money
    claims_paid: Money
  }
  risk: { open_risk_events: number; max_deployed_pct: Ratio; max_drawdown_pct: Ratio }
  empty: boolean
}

export interface PolicyRow {
  id: string
  policy_number: string
  underlying: string
  structure: string
  status: 'PENDING' | 'OPEN' | 'CLOSING' | 'SETTLED' | 'FAILED' | 'LEG_RISK'
  contracts: number
  opening_credit: Money | null
  max_loss: Money | null
  capital_reserve: Money | null
  expiration: string | null
  realized_pnl: Money | null
  settlement_reason: string | null
}

export interface PolicyList {
  as_of: string
  policies: PolicyRow[]
  returned: number
  total: number
  empty: boolean
}

export interface Headroom {
  used: string
  limit: string
  utilization_pct?: string
  headroom: string
}

export interface Exposure {
  as_of: string
  nav: Money | null
  open_policies: number
  limits: Record<string, Headroom>
  concentration: { limit_per_underlying: Money | null; by_underlying: Record<string, Headroom> }
  reserve_invariant: {
    holds: boolean
    detail: string
    held_reserves: Money | null
    exposed_max_loss: Money | null
  }
  empty: boolean
}

export interface DecisionRow {
  id: string
  correlation_id: string
  candidate_id: string | null
  action: 'WRITE' | 'DECLINE'
  confidence: Ratio | null
  requested_contracts: number | null
  rationale: string | null
  identified_risks: string[]
  declined_reason: string | null
  model_version: string | null
  prompt_sha256: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  latency_ms: number | null
  retry_count: number
  created_at: string
}

export interface DecisionList {
  as_of: string
  decisions: DecisionRow[]
  returned: number
  empty: boolean
}

export interface VerdictRow {
  id: string
  correlation_id: string
  proposal_hash: string
  verdict: 'APPROVE' | 'REJECT'
  approved_contracts: number
  reject_reasons: string[]
  issued_at: string
  signed: boolean
}

export interface VerdictFeed {
  as_of: string
  decisions: VerdictRow[]
  approved: number
  vetoed: number
  empty: boolean
}

/**
 * OPS-008 — per-rule veto counts.
 *
 * `failures` counts rules, `proposals_blocked` counts proposals. They differ
 * whenever one proposal broke several limits at once, and the difference is
 * the interesting part: a rule can be loud without stopping much.
 */
export interface RuleVetoCount {
  rule_id: string
  name: string | null
  severity: 'HARD' | 'SOFT' | null
  failures: number
  proposals_blocked: number
}

export interface VetoMetrics {
  as_of: string
  proposals_evaluated: number
  approved: number
  vetoed: number
  veto_rate: number | null
  rules_exercised: number
  by_rule: RuleVetoCount[]
}

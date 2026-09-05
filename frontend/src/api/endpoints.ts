/**
 * Every backend path in one place (SRS §19.2).
 *
 * Nothing else in the app writes a URL string. When a path moves, it moves
 * here, and the API ids stay next to it so a panel can be traced back to the
 * requirement it satisfies.
 */

export const API_PREFIX = '/api'

export const endpoints = {
  // Dashboard — API-010..012
  overview: `${API_PREFIX}/dashboard/overview`,
  equityCurve: (range: '1d' | 'all') => `${API_PREFIX}/dashboard/equity-curve?range=${range}`,
  stats: `${API_PREFIX}/dashboard/stats`,

  // Policies — API-020..022
  policies: `${API_PREFIX}/policies`,
  policy: (policyId: string) => `${API_PREFIX}/policies/${policyId}`,
  closePolicy: (policyId: string) => `${API_PREFIX}/policies/${policyId}/close`,

  // Underwriting — API-030..033
  candidates: `${API_PREFIX}/underwriting/candidates`,
  decisions: `${API_PREFIX}/underwriting/decisions`,
  runCycle: `${API_PREFIX}/underwriting/run`,
  replay: (decisionId: string) => `${API_PREFIX}/underwriting/replay/${decisionId}`,

  // Risk & Kernel — API-040..045
  exposure: `${API_PREFIX}/risk/exposure`,
  limits: `${API_PREFIX}/risk/limits`,
  riskEvents: `${API_PREFIX}/risk/events`,
  kernelDecisions: `${API_PREFIX}/kernel/decisions`,
  kernelDecision: (id: string) => `${API_PREFIX}/kernel/decisions/${id}`,
  kernelSimulate: `${API_PREFIX}/kernel/simulate`,

  // Orders, positions, P&L — API-050..053
  orders: `${API_PREFIX}/orders`,
  positions: `${API_PREFIX}/positions`,
  reconcile: `${API_PREFIX}/positions/reconcile`,
  pnl: `${API_PREFIX}/pnl`,

  // Audit — API-060..062
  auditLog: `${API_PREFIX}/audit/log`,
  auditVerify: `${API_PREFIX}/audit/verify`,
  auditExport: (format: 'json' | 'csv') => `${API_PREFIX}/audit/export?format=${format}`,

  // System — API-070..078
  health: '/health',
  healthDeep: `${API_PREFIX}/health/deep`,
  status: `${API_PREFIX}/system/status`,
  setMode: `${API_PREFIX}/system/mode`,
  killSwitch: `${API_PREFIX}/system/kill-switch`,
  schedulerRuns: `${API_PREFIX}/scheduler/runs`,
  config: `${API_PREFIX}/config`,
  metrics: `${API_PREFIX}/metrics`,
  vetoMetrics: `${API_PREFIX}/metrics/vetoes`,
} as const

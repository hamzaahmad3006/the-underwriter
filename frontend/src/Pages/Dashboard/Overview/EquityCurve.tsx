import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { EmptyState } from '../../../components/ui/EmptyState'

export interface EquityPoint {
  at: string
  equity: string | null
  realized_pnl_cum: string | null
  drawdown_pct: string | null
}

interface Props {
  points: EquityPoint[]
  baseline: string | null
}

/**
 * §21.6 — the equity curve.
 *
 * One series, one reference line at the starting balance, and nothing else.
 * A trading dashboard invites decoration, and every extra mark here would
 * compete with the only comparison that matters: are we above the line we
 * started on.
 *
 * Nothing is interpolated. A gap in the series is a gap in the record, and
 * smoothing it would draw equity the account never had.
 */
export function EquityCurve({ points, baseline }: Props) {
  if (points.length === 0) {
    return (
      <EmptyState
        title="No equity history yet"
        reason="Points are written once per reconciliation, every five minutes while the market is open."
        hint="The curve starts the first time the desk reconciles against the broker."
      />
    )
  }

  // Recharts needs numbers. These are display coordinates only — every figure
  // shown as text elsewhere comes from the Decimal string, unrounded.
  const data = points.map((point) => ({
    at: new Date(point.at).getTime(),
    equity: point.equity === null ? null : Number(point.equity),
    realized: point.realized_pnl_cum === null ? null : Number(point.realized_pnl_cum),
  }))

  const start = baseline === null ? null : Number(baseline)
  const values = data.map((d) => d.equity).filter((v): v is number => v !== null)
  const low = Math.min(...values, start ?? Infinity)
  const high = Math.max(...values, start ?? -Infinity)
  const pad = Math.max((high - low) * 0.1, 1)

  return (
    <div className="h-56 w-full sm:h-64">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-accent)" stopOpacity={0.25} />
              <stop offset="100%" stopColor="var(--color-accent)" stopOpacity={0} />
            </linearGradient>
          </defs>

          <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />

          <XAxis
            dataKey="at"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(value: number) =>
              new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            }
            tick={{ fill: 'var(--color-faint)', fontSize: 11 }}
            stroke="var(--color-line)"
            minTickGap={40}
          />
          <YAxis
            domain={[low - pad, high + pad]}
            tickFormatter={(value: number) => `$${Math.round(value).toLocaleString()}`}
            tick={{ fill: 'var(--color-faint)', fontSize: 11 }}
            stroke="var(--color-line)"
            width={68}
          />

          {start !== null && (
            <ReferenceLine
              y={start}
              stroke="var(--color-muted)"
              strokeDasharray="4 4"
              label={{
                value: 'baseline',
                position: 'insideTopLeft',
                fill: 'var(--color-faint)',
                fontSize: 11,
              }}
            />
          )}

          <Tooltip
            contentStyle={{
              background: 'var(--color-panel)',
              border: '1px solid var(--color-line)',
              borderRadius: 4,
              fontSize: 12,
            }}
            labelStyle={{ color: 'var(--color-muted)' }}
            labelFormatter={(label) => new Date(Number(label)).toLocaleString()}
            formatter={(value) => [
              `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2 })}`,
              'Equity',
            ]}
          />

          <Area
            type="monotone"
            dataKey="equity"
            stroke="var(--color-accent)"
            strokeWidth={2}
            fill="url(#equityFill)"
            // A gap in the record stays a gap on the chart.
            connectNulls={false}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

"""Dashboard endpoints — API-010, API-011, API-012.

Live against the book. An empty book returns honest zeros with the real shape
rather than a 503, because "no cycle has run yet" is a state the dashboard
should be able to display — and UI-006 wants the panel to say so in words.

Every payload carries `as_of` (UI-002). Money serialises as a string because
the backend computes in Decimal and the browser must not round a reserve on
the way to the screen.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from underwriter.db import session_scope
from underwriter.db.queries import book_summary, equity_curve
from underwriter.kernel.limits import DEFAULT_LIMITS


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def overview() -> dict[str, Any]:
    """API-010 — the executive summary tiles."""
    with session_scope() as session:
        book = book_summary(session)

        return {
            "as_of": book.as_of.isoformat(),
            "capital": {
                "baseline_equity": _money(book.baseline_equity),
                "total_equity": _money(book.equity),
                "available": _money(book.available),
                "reserved": _money(book.reserved),
                "at_risk_pct": str(book.at_risk_pct),
            },
            "pnl": {
                "realized": _money(book.realized_pnl),
                "unrealized": _money(book.unrealized_pnl),
            },
            "book": {
                "open_policies": book.open_policies,
                "closed_policies": book.closed_policies,
                "policies_written": book.policies_written,
            },
            "performance": {
                "wins": book.wins,
                "losses": book.losses,
                "win_rate": _money(book.win_rate),
                # The underwriting measure, reported next to win rate rather
                # than instead of it: a high hit rate with a poor loss ratio is
                # exactly what a badly-run credit book looks like.
                "loss_ratio": _money(book.loss_ratio),
                "premiums_written": _money(book.premiums_written),
                "claims_paid": _money(book.claims_paid),
            },
            "risk": {
                "open_risk_events": book.open_risk_events,
                "max_deployed_pct": str(DEFAULT_LIMITS.max_deployed_pct),
                "max_drawdown_pct": str(DEFAULT_LIMITS.max_drawdown_pct),
            },
            "empty": book.policies_written == 0,
        }


def equity_series(range_: str) -> dict[str, Any]:
    """API-011 — the equity curve.

    Points come from `pnl_records`, one per reconciliation. Nothing is
    interpolated: a gap in the series is a gap in the record, and smoothing it
    would invent equity the account never had.
    """
    with session_scope() as session:
        rows = equity_curve(session)
        book = book_summary(session)

        points = [
            {
                "at": row.recorded_at.isoformat(),
                "equity": _money(row.equity),
                "realized_pnl_cum": _money(row.realized_pnl_cum),
                "unrealized_pnl": _money(row.unrealized_pnl),
                "drawdown_pct": _money(row.drawdown_pct),
            }
            for row in rows
        ]

        return {
            "as_of": book.as_of.isoformat(),
            "range": range_,
            "baseline_equity": _money(book.baseline_equity),
            "points": points,
            "empty": not points,
        }


def stats() -> dict[str, Any]:
    """API-012 — win rate, loss ratio, average credit and hold, Brier.

    Brier is absent until settled policies exist to score against. Reporting a
    calibration number computed from nothing would be worse than reporting none.
    """
    with session_scope() as session:
        book = book_summary(session)

        return {
            "as_of": book.as_of.isoformat(),
            "settled_policies": book.closed_policies,
            "win_rate": _money(book.win_rate),
            "loss_ratio": _money(book.loss_ratio),
            "premiums_written": _money(book.premiums_written),
            "claims_paid": _money(book.claims_paid),
            "realized_pnl": _money(book.realized_pnl),
            "brier_score": None,
            "sample_note": (
                "Four sessions is not a statistically meaningful sample. These figures "
                "describe what happened, not what to expect."
            ),
            "empty": book.closed_policies == 0,
        }

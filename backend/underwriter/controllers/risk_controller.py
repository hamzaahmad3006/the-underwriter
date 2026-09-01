"""Risk endpoints — API-040 and API-042.

API-041's rule table lives in `kernel_controller`, because it comes off the
Kernel itself rather than out of the database.

The reserve invariant is reported here rather than hidden in a health check.
DB-INV-1 failing means the book's own accounting disagrees with itself, which
makes every capital limit above it decorative — that belongs on the risk page,
in front of whoever is watching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from underwriter.db import session_scope
from underwriter.db.models import RiskEvent
from underwriter.db.queries import book_summary, exposure_summary
from underwriter.domain.money import ZERO
from underwriter.kernel.limits import DEFAULT_LIMITS


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _headroom(used: Decimal, ceiling: Decimal) -> dict[str, Any]:
    """How much of a limit is consumed, and how much is left."""
    utilization = (
        (used / ceiling * Decimal("100")).quantize(Decimal("0.01")) if ceiling > ZERO else ZERO
    )
    return {
        "used": str(used),
        "limit": str(ceiling),
        "utilization_pct": str(utilization),
        "headroom": str(max(ZERO, ceiling - used)),
    }


def exposure() -> dict[str, Any]:
    """API-040 — concentration, reserve utilisation, per-limit headroom."""
    with session_scope() as session:
        summary = exposure_summary(session)
        book = book_summary(session)

        nav = book.equity or book.baseline_equity or ZERO
        deployable = nav * DEFAULT_LIMITS.max_deployed_pct
        concentration_ceiling = deployable * DEFAULT_LIMITS.max_underlying_concentration

        return {
            "as_of": summary.as_of.isoformat(),
            "nav": _money(nav) if nav > ZERO else None,
            "open_policies": summary.open_policies,
            "limits": {
                "SK-001_deployed_capital": _headroom(summary.total_reserved, deployable),
                "SK-007_portfolio_max_loss": _headroom(
                    summary.portfolio_max_loss, nav * DEFAULT_LIMITS.max_portfolio_risk_pct
                ),
                "SK-005_open_policies": {
                    "used": str(summary.open_policies),
                    "limit": str(DEFAULT_LIMITS.max_open_policies),
                    "headroom": str(
                        max(0, DEFAULT_LIMITS.max_open_policies - summary.open_policies)
                    ),
                },
            },
            "concentration": {
                "limit_per_underlying": _money(concentration_ceiling),
                "by_underlying": {
                    name: _headroom(amount, concentration_ceiling)
                    for name, amount in sorted(summary.by_underlying.items())
                },
            },
            # DB-INV-1. If this is false, nothing above it means anything.
            "reserve_invariant": {
                "holds": summary.reserve_invariant_holds,
                "detail": summary.reserve_invariant_detail,
                "held_reserves": _money(summary.total_reserved),
                "exposed_max_loss": _money(summary.portfolio_max_loss),
            },
            "greeks": {
                "note": "Portfolio Greeks are aggregated from open positions once a cycle "
                "has recorded them.",
                "net_delta": None,
                "net_vega": None,
            },
            "empty": summary.open_policies == 0,
        }


def events(severity: str | None, limit: int) -> dict[str, Any]:
    """API-042 — the risk event feed, newest first."""
    with session_scope() as session:
        query = select(RiskEvent).order_by(RiskEvent.occurred_at.desc()).limit(limit)
        if severity:
            query = query.where(RiskEvent.severity == severity)

        rows = session.execute(query).scalars().all()
        unresolved = [row for row in rows if row.resolved_at is None]

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "events": [
                {
                    "id": row.id,
                    "occurred_at": row.occurred_at.isoformat(),
                    "event_type": row.event_type,
                    "severity": row.severity,
                    "policy_id": row.policy_id,
                    "detail": row.detail_json,
                    "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
                }
                for row in rows
            ],
            "returned": len(rows),
            "unresolved": len(unresolved),
            "empty": not rows,
        }

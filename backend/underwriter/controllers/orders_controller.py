"""Order, position and P&L endpoints — API-050 … API-053.

The order log is the audit surface for execution: every row carries the full
request and response, and the verdict id that authorised it. NFR-008 is a
schema property here — `orders.kernel_decision_id` is NOT NULL — so this
endpoint cannot show an order with no verdict behind it even if one existed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from underwriter.db import session_scope
from underwriter.db.models import Fill, Order, PnlRecord, Policy, PositionSnapshot
from underwriter.db.queries import book_summary
from underwriter.domain.money import ZERO


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def list_orders(policy_id: str | None, status: str | None) -> dict[str, Any]:
    """API-050 — orders with their full request/response audit."""
    with session_scope() as session:
        query = select(Order).order_by(Order.created_at.desc()).limit(200)
        if policy_id:
            query = query.where(Order.policy_id == policy_id)
        if status:
            query = query.where(Order.status == status)

        rows = session.execute(query).scalars().all()
        order_ids = [row.id for row in rows]
        fills = (
            session.execute(select(Fill).where(Fill.order_id.in_(order_ids))).scalars().all()
            if order_ids
            else []
        )
        fills_by_order: dict[str, int] = {}
        for fill in fills:
            fills_by_order[fill.order_id] = fills_by_order.get(fill.order_id, 0) + 1

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "orders": [
                {
                    "id": row.id,
                    "policy_id": row.policy_id,
                    "client_order_id": row.client_order_id,
                    "alpaca_order_id": row.alpaca_order_id,
                    "kernel_decision_id": row.kernel_decision_id,
                    "intent": row.intent,
                    "order_class": row.order_class,
                    "status": row.status,
                    "limit_price": _money(row.limit_price),
                    "filled_qty": _money(row.filled_qty),
                    "filled_avg_price": _money(row.filled_avg_price),
                    "attempt": row.attempt,
                    "terminal": bool(row.terminal),
                    "error_code": row.error_code,
                    "error_message": row.error_message,
                    "fills": fills_by_order.get(row.id, 0),
                    "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                }
                for row in rows
            ],
            "returned": len(rows),
            "empty": not rows,
        }


def positions() -> dict[str, Any]:
    """API-051 — the last reconciliation, with divergence flags.

    A position with no `matched_policy_id` is an orphan: the broker holds
    something the book cannot explain. That is F-19, always CRITICAL, and it
    forces MANAGE_ONLY — so it is surfaced first rather than buried in a list.
    """
    with session_scope() as session:
        latest = session.execute(
            select(PositionSnapshot.taken_at).order_by(PositionSnapshot.taken_at.desc()).limit(1)
        ).scalar_one_or_none()

        rows = (
            session.execute(select(PositionSnapshot).where(PositionSnapshot.taken_at == latest))
            .scalars()
            .all()
            if latest is not None
            else []
        )
        orphans = [row for row in rows if row.matched_policy_id is None]

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "reconciled_at": latest.isoformat() if latest else None,
            "positions": [
                {
                    "symbol": row.symbol,
                    "qty": _money(row.qty),
                    "avg_entry_price": _money(row.avg_entry_price),
                    "market_value": _money(row.market_value),
                    "unrealized_pl": _money(row.unrealized_pl),
                    "matched_policy_id": row.matched_policy_id,
                    "orphan": row.matched_policy_id is None,
                }
                for row in rows
            ],
            "orphans": len(orphans),
            "empty": not rows,
        }


def reconcile() -> dict[str, Any]:
    """API-052 — run reconciliation now, rather than waiting for the tick."""
    from underwriter.controllers import operator_controller

    return operator_controller.force_reconcile()


def pnl(granularity: str) -> dict[str, Any]:
    """API-053 — realised and unrealised P&L, by policy or by day."""
    with session_scope() as session:
        book = book_summary(session)

        if granularity == "policy":
            settled = (
                session.execute(select(Policy).where(Policy.status == "SETTLED")).scalars().all()
            )
            breakdown = [
                {
                    "policy_number": policy.policy_number,
                    "underlying": policy.underlying,
                    "opening_credit": _money(policy.opening_credit),
                    "closing_debit": _money(policy.closing_debit),
                    "realized_pnl": _money(policy.realized_pnl),
                    "settlement_reason": policy.settlement_reason,
                    "won": (policy.realized_pnl or ZERO) > ZERO,
                    "closed_at": policy.closed_at.isoformat() if policy.closed_at else None,
                }
                for policy in settled
            ]
        else:
            records = (
                session.execute(select(PnlRecord).order_by(PnlRecord.recorded_at.asc()))
                .scalars()
                .all()
            )
            breakdown = [
                {
                    "at": record.recorded_at.isoformat(),
                    "equity": _money(record.equity),
                    "realized_pnl_cum": _money(record.realized_pnl_cum),
                    "unrealized_pnl": _money(record.unrealized_pnl),
                    "drawdown_pct": _money(record.drawdown_pct),
                }
                for record in records
            ]

        return {
            "as_of": book.as_of.isoformat(),
            "granularity": granularity,
            "realized_total": _money(book.realized_pnl),
            "unrealized_total": _money(book.unrealized_pnl),
            "breakdown": breakdown,
            "empty": not breakdown,
        }

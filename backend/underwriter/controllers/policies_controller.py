"""Policy endpoints — API-020, API-021, API-022.

API-022 *requests* a close; it does not perform one. FR-066 routes every
state-changing action through the Kernel, including an operator's own, so this
endpoint must never grow a direct execution path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from underwriter.db import session_scope
from underwriter.db.models import Fill, Order, Policy, PolicyLeg
from underwriter.middleware.error_handler import EndpointNotReadyError

LIFECYCLE = (
    "Candidate",
    "Underwritten",
    "Kernel-Approved",
    "Executed",
    "Managed",
    "Closing",
    "Settled",
)


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _policy_row(policy: Policy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "policy_number": policy.policy_number,
        "underlying": policy.underlying,
        "structure": policy.structure,
        "status": policy.status,
        "contracts": policy.contracts,
        "opening_credit": _money(policy.opening_credit),
        "max_profit": _money(policy.max_profit),
        "max_loss": _money(policy.max_loss),
        "capital_reserve": _money(policy.capital_reserve),
        "expiration": policy.expiration,
        "opened_at": policy.opened_at.isoformat() if policy.opened_at else None,
        "closed_at": policy.closed_at.isoformat() if policy.closed_at else None,
        "closing_debit": _money(policy.closing_debit),
        "realized_pnl": _money(policy.realized_pnl),
        "settlement_reason": policy.settlement_reason,
        "predicted_confidence": _money(policy.predicted_confidence),
        "correlation_id": policy.correlation_id,
    }


def list_policies(
    status: str | None, underlying: str | None, limit: int, offset: int
) -> dict[str, Any]:
    """API-020 — the underwriting book."""
    with session_scope() as session:
        query = select(Policy).order_by(Policy.created_at.desc())
        if status:
            query = query.where(Policy.status == status)
        if underlying:
            query = query.where(Policy.underlying == underlying)

        rows = session.execute(query.limit(limit).offset(offset)).scalars().all()
        total = len(session.execute(select(Policy.id)).scalars().all())

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "policies": [_policy_row(policy) for policy in rows],
            "returned": len(rows),
            "total": total,
            "empty": total == 0,
        }


def get_policy(policy_id: str) -> dict[str, Any]:
    """API-021 — one policy with legs, orders, fills and its lifecycle."""
    with session_scope() as session:
        policy = session.get(Policy, policy_id)
        if policy is None:
            raise EndpointNotReadyError(f"Policy {policy_id}", "no policy exists with that id")

        legs = (
            session.execute(select(PolicyLeg).where(PolicyLeg.policy_id == policy_id))
            .scalars()
            .all()
        )
        orders = session.execute(select(Order).where(Order.policy_id == policy_id)).scalars().all()
        order_ids = [order.id for order in orders]
        fills = (
            session.execute(select(Fill).where(Fill.order_id.in_(order_ids))).scalars().all()
            if order_ids
            else []
        )

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "policy": _policy_row(policy),
            "legs": [
                {
                    "symbol": leg.option_symbol,
                    "side": leg.side,
                    "position_intent": leg.position_intent,
                    "ratio_qty": leg.ratio_qty,
                    "strike": _money(leg.strike),
                    "expiration": leg.expiration,
                    "option_type": leg.option_type,
                    "open_price": _money(leg.open_price),
                    "close_price": _money(leg.close_price),
                    "open_delta": _money(leg.open_delta),
                    "open_iv": _money(leg.open_iv),
                }
                for leg in legs
            ],
            "orders": [
                {
                    "id": order.id,
                    "client_order_id": order.client_order_id,
                    "alpaca_order_id": order.alpaca_order_id,
                    # NFR-008: there is no order row without one.
                    "kernel_decision_id": order.kernel_decision_id,
                    "intent": order.intent,
                    "status": order.status,
                    "limit_price": _money(order.limit_price),
                    "filled_qty": _money(order.filled_qty),
                    "filled_avg_price": _money(order.filled_avg_price),
                    "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
                }
                for order in orders
            ],
            "fills": [
                {
                    "symbol": fill.option_symbol,
                    "side": fill.side,
                    "qty": _money(fill.qty),
                    "price": _money(fill.price),
                    "filled_at": fill.filled_at.isoformat() if fill.filled_at else None,
                }
                for fill in fills
            ],
            "lifecycle": list(LIFECYCLE),
        }


def request_close(policy_id: str, reason: str) -> dict[str, Any]:
    """API-022 — ask the Kernel to authorise a close.

    Deliberately still unavailable. The path exists in the management cycle,
    but exposing it here before the scheduler owns the book would create a
    second way to act on a policy — and a second path is a second thing that
    can be wrong.
    """
    raise EndpointNotReadyError(
        "Operator-requested closes",
        "exits currently run through the management cycle; a second path would be "
        "a second thing that can be wrong (FR-066)",
    )

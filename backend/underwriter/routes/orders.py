"""Order, position and P&L routes — API-050 … API-053."""

from typing import Any

from fastapi import APIRouter, Depends, Query

from underwriter.controllers import orders_controller
from underwriter.middleware import require_operator

router = APIRouter(tags=["orders"])


@router.get("/orders", summary="API-050 orders with full audit")
def list_orders(policy_id: str | None = None, status: str | None = None) -> dict[str, Any]:
    return orders_controller.list_orders(policy_id, status)


@router.get("/positions", summary="API-051 reconciled positions")
def positions() -> dict[str, Any]:
    return orders_controller.positions()


@router.post("/positions/reconcile", summary="API-052 force reconciliation")
def reconcile(actor: str = Depends(require_operator)) -> dict[str, Any]:
    return orders_controller.reconcile()


@router.get("/pnl", summary="API-053 realised and unrealised P&L")
def pnl(granularity: str = Query(default="policy", pattern="^(policy|day)$")) -> dict[str, Any]:
    return orders_controller.pnl(granularity)

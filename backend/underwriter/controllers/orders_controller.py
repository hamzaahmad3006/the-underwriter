"""Order, position and P&L endpoints — API-050 … API-053."""

from __future__ import annotations

from typing import Any

from underwriter.middleware.error_handler import EndpointNotReadyError


def list_orders(policy_id: str | None, status: str | None) -> dict[str, Any]:
    """API-050 — orders with full request/response audit."""
    raise EndpointNotReadyError(
        "The order log",
        "the SQLite persistence layer is not built yet (§18)",
    )


def positions() -> dict[str, Any]:
    """API-051 — reconciled positions with divergence flags."""
    raise EndpointNotReadyError(
        "Positions",
        "the Alpaca data layer is not wired yet (§11.1, ROAD-D1-01)",
    )


def reconcile() -> dict[str, Any]:
    """API-052 — force reconciliation now."""
    raise EndpointNotReadyError(
        "Forced reconciliation",
        "the Alpaca data layer is not wired yet (§11.1, ROAD-D1-01)",
    )


def pnl(granularity: str) -> dict[str, Any]:
    """API-053 — realised and unrealised P&L."""
    raise EndpointNotReadyError(
        "P&L",
        "the SQLite persistence layer is not built yet (§18)",
    )

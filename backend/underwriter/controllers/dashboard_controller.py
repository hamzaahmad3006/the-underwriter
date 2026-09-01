"""Dashboard endpoints — API-010, API-011, API-012."""

from __future__ import annotations

from typing import Any

from underwriter.middleware.error_handler import EndpointNotReadyError


def overview() -> dict[str, Any]:
    """API-010 — executive summary tiles."""
    raise EndpointNotReadyError(
        "The executive overview",
        "the SQLite persistence layer is not built yet (§18)",
    )


def equity_curve(range_: str) -> dict[str, Any]:
    """API-011 — equity time series."""
    raise EndpointNotReadyError(
        "The equity curve",
        "the SQLite persistence layer is not built yet (§18)",
    )


def stats() -> dict[str, Any]:
    """API-012 — win rate, loss ratio, average credit and hold, Brier."""
    raise EndpointNotReadyError(
        "Performance statistics",
        "the SQLite persistence layer is not built yet (§18)",
    )

"""Risk endpoints — API-040, API-041, API-042."""

from __future__ import annotations

from typing import Any

from underwriter.middleware.error_handler import EndpointNotReadyError


def exposure() -> dict[str, Any]:
    """API-040 — portfolio Greeks, concentration, reserve utilisation, headroom."""
    raise EndpointNotReadyError(
        "Portfolio exposure",
        "the SQLite persistence layer is not built yet (§18)",
    )


def events(severity: str | None, limit: int) -> dict[str, Any]:
    """API-042 — the risk event feed."""
    raise EndpointNotReadyError(
        "The risk event feed",
        "the SQLite persistence layer is not built yet (§18)",
    )

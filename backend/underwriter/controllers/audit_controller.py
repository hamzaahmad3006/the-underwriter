"""Audit endpoints — API-060, API-061, API-062.

API-062 exists so a judge can take the whole ledger away and check it.
"""

from __future__ import annotations

from typing import Any

from underwriter.middleware.error_handler import EndpointNotReadyError


def log(correlation_id: str | None, actor: str | None, limit: int) -> dict[str, Any]:
    """API-060 — the audit trail."""
    raise EndpointNotReadyError(
        "The audit trail",
        "the SQLite persistence layer is not built yet (§18)",
    )


def verify() -> dict[str, Any]:
    """API-061 — walk the hash chain and report the first break."""
    raise EndpointNotReadyError(
        "Hash chain verification",
        "the SQLite persistence layer is not built yet (§18)",
    )


def export(format_: str) -> dict[str, Any]:
    """API-062 — full ledger export for judges."""
    raise EndpointNotReadyError(
        "Ledger export",
        "the SQLite persistence layer is not built yet (§18)",
    )

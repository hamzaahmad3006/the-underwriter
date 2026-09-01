"""Underwriting endpoints — API-030 … API-033.

API-033 is the auditability proof: re-run the Actuary and the Kernel over
stored inputs and diff the result. The diff must be empty (NFR-007).
"""

from __future__ import annotations

from typing import Any

from underwriter.middleware.error_handler import EndpointNotReadyError


def candidates(correlation_id: str | None) -> dict[str, Any]:
    """API-030 — Actuary-priced candidates, including rejected ones."""
    raise EndpointNotReadyError(
        "The candidate set",
        "the Alpaca data layer is not wired yet (§11.1, ROAD-D1-01)",
    )


def decisions(limit: int, offset: int) -> dict[str, Any]:
    """API-031 — LLM decisions with rationale."""
    raise EndpointNotReadyError(
        "Underwriting decisions",
        "the SQLite persistence layer is not built yet (§18)",
    )


def run_cycle(dry_run: bool, force_underlying: str | None) -> dict[str, Any]:
    """API-032 — trigger a cycle now (the safe demo trigger)."""
    raise EndpointNotReadyError(
        "Manual cycle runs",
        "the Alpaca data layer is not wired yet (§11.1, ROAD-D1-01)",
    )


def replay(decision_id: str) -> dict[str, Any]:
    """API-033 — replay stored inputs and diff against the original."""
    raise EndpointNotReadyError(
        "Decision replay",
        "the SQLite persistence layer is not built yet (§18)",
    )

"""Underwriting endpoints — API-030 … API-033.

API-030 is live: it runs the real Market Data Layer and the real Actuary, so
what comes back is what the LLM would be handed this minute. The discards come
back with it, because UI-006 needs an empty candidate set to explain itself and
because "nothing qualified today" is a result, not a failure (FR-026).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from underwriter.actuary.engine import price_put_credit_spreads
from underwriter.controllers.serializers import discard_to_dict, proposal_to_dict
from underwriter.data.alpaca_source import AlpacaMarketData
from underwriter.data.credentials import has_credentials
from underwriter.data.snapshot import DEFAULT_SNAPSHOT_CONFIG, build_snapshot
from underwriter.middleware.error_handler import EndpointNotReadyError


def candidates(correlation_id: str | None) -> dict[str, Any]:
    """API-030 — Actuary-priced candidates, including the rejected ones."""
    if not has_credentials():
        raise EndpointNotReadyError(
            "The candidate set",
            "no Alpaca credentials are configured — set ALPACA_API_KEY and "
            "ALPACA_SECRET_KEY (ROAD-D0-02)",
        )

    result = build_snapshot(AlpacaMarketData(), config=DEFAULT_SNAPSHOT_CONFIG)

    if not result.ok or result.snapshot is None:
        # An abort is a recorded outcome with a reason, never an error.
        return {
            "as_of": datetime.now(UTC).isoformat(),
            "aborted": result.aborted,
            "detail": result.detail,
            "session": {
                "is_open": result.session.is_open,
                "minutes_since_open": result.session.minutes_since_open,
                "minutes_to_close": result.session.minutes_to_close,
            },
            "candidates": [],
            "discards": [],
        }

    priced = price_put_credit_spreads(result.snapshot, universe=DEFAULT_SNAPSHOT_CONFIG.universe)

    return {
        "as_of": result.snapshot.as_of.isoformat(),
        "snapshot_hash": result.snapshot.snapshot_hash,
        "aborted": None,
        "detail": result.detail,
        "contracts_seen": result.contracts_seen,
        "volatility": {
            name: {
                "realized_vol": str(ctx.realized_vol) if ctx.realized_vol else None,
                "implied_vol": str(ctx.implied_vol) if ctx.implied_vol else None,
                "iv_rank": str(ctx.iv_rank) if ctx.iv_rank else None,
                "measure": ctx.measure,
                "detail": ctx.detail,
            }
            for name, ctx in result.volatility.items()
        },
        "candidates": [proposal_to_dict(p) for p in priced.proposals],
        "discards": [discard_to_dict(d) for d in priced.discards],
    }


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
        "the AI Underwriter is not wired yet — resolve ASM-006 first (ROAD-D0-06)",
    )


def replay(decision_id: str) -> dict[str, Any]:
    """API-033 — replay stored inputs and diff against the original."""
    raise EndpointNotReadyError(
        "Decision replay",
        "the SQLite persistence layer is not built yet (§18)",
    )

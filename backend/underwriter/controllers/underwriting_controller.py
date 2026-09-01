"""Underwriting endpoints — API-030 … API-033.

API-030 is live: it runs the real Market Data Layer and the real Actuary, so
what comes back is what the LLM would be handed this minute. The discards come
back with it, because UI-006 needs an empty candidate set to explain itself and
because "nothing qualified today" is a result, not a failure (FR-026).
"""

from __future__ import annotations

import os
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
    """API-031 — LLM decisions with rationale and full provenance (FR-043)."""
    from sqlalchemy import select

    from underwriter.db import session_scope
    from underwriter.db.models import UnderwritingDecision

    with session_scope() as session:
        rows = (
            session.execute(
                select(UnderwritingDecision)
                .order_by(UnderwritingDecision.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "decisions": [
                {
                    "id": row.id,
                    "correlation_id": row.correlation_id,
                    "candidate_id": row.candidate_id,
                    "action": row.action,
                    "confidence": str(row.confidence) if row.confidence else None,
                    "requested_contracts": row.requested_contracts,
                    "rationale": row.rationale,
                    "identified_risks": row.identified_risks_json or [],
                    "declined_reason": row.declined_reason,
                    "model": row.model,
                    "model_version": row.model_version,
                    "temperature": str(row.temperature) if row.temperature else None,
                    # What makes the decision reproducible six weeks later.
                    "prompt_sha256": row.prompt_sha256,
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "latency_ms": row.latency_ms,
                    "retry_count": row.retry_count,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ],
            "returned": len(rows),
            "empty": not rows,
        }


def run_cycle(dry_run: bool, force_underlying: str | None) -> dict[str, Any]:
    """API-032 — trigger a cycle now.

    `dry_run=true` runs the whole pipeline to a Kernel verdict and transmits
    nothing. It is the safe demo trigger and it exercises exactly the same code
    path as a scheduled cycle up to the point of execution — which is what
    makes it worth demonstrating rather than a mock of one.

    Either way the cycle is persisted. A dry run that left no trace would be a
    demo of something other than the system.
    """
    from underwriter.agent.client import GroqClient, LLMUnavailableError
    from underwriter.agent.underwriter import AIUnderwriter
    from underwriter.cycle.bootstrap import build_kernel_context
    from underwriter.cycle.underwrite import run_underwriting_cycle
    from underwriter.data.snapshot import SnapshotConfig

    if not has_credentials():
        raise EndpointNotReadyError(
            "Manual cycle runs",
            "no Alpaca credentials are configured (ROAD-D0-02)",
        )

    try:
        agent = AIUnderwriter(GroqClient())
    except (LLMUnavailableError, ValueError) as exc:
        raise EndpointNotReadyError("Manual cycle runs", str(exc)) from exc

    secret = os.environ.get("KERNEL_SIGNING_SECRET", "").strip()
    if not secret:
        raise EndpointNotReadyError(
            "Manual cycle runs", "KERNEL_SIGNING_SECRET is unset; no verdict can be minted"
        )

    config = (
        SnapshotConfig(universe=(force_underlying,))
        if force_underlying
        else DEFAULT_SNAPSHOT_CONFIG
    )

    report = run_underwriting_cycle(
        source=AlpacaMarketData(),
        agent=agent,
        context=build_kernel_context(),
        secret=secret,
        # No execution engine on this path even when dry_run is false: a demo
        # trigger that can transmit is not a demo trigger.
        execution=None,
        snapshot_config=config,
        dry_run=True,
        persist=True,
    )

    return {
        "as_of": report.started_at.isoformat(),
        "correlation_id": report.correlation_id,
        "status": report.status,
        "outcome": report.outcome,
        "detail": report.detail,
        "steps": list(report.steps),
        "snapshot_hash": report.snapshot_hash,
        "candidates_priced": report.candidates_priced,
        "candidates_discarded": report.candidates_discarded,
        "decision": report.decision,
        "verdict": report.verdict,
        "approved_contracts": report.approved_contracts,
        "reject_reasons": list(report.reject_reasons),
        "executed": False,
        "duration_ms": report.duration_ms,
    }


def replay(decision_id: str) -> dict[str, Any]:
    """API-033 — the auditability proof. The diff must be empty (NFR-007).

    A mismatch is not a server error. It is a finding, and a serious one: it
    means the same inputs stopped producing the same outputs, which is the
    property the whole audit story rests on. It raises a CRITICAL risk event
    and is reported plainly rather than hidden behind a 500.
    """
    from underwriter.cycle.recorder import CycleRecorder
    from underwriter.cycle.replay import ReplayUnavailableError, replay_decision
    from underwriter.db import session_scope

    with session_scope() as session:
        try:
            result = replay_decision(session, decision_id)
        except ReplayUnavailableError as exc:
            raise EndpointNotReadyError("Decision replay", str(exc)) from exc

        if not result.deterministic:
            CycleRecorder(session, f"replay:{decision_id}").risk_event(
                "REPLAY_MISMATCH",
                "CRITICAL",
                detail={
                    "decision_id": decision_id,
                    "diff": [
                        {"field": d.field, "recorded": d.recorded, "replayed": d.replayed}
                        for d in result.diff
                    ],
                },
            )

        return {
            "as_of": result.as_of.isoformat(),
            "decision_id": result.decision_id,
            "deterministic": result.deterministic,
            "snapshot_hash": result.snapshot_hash,
            "replayed_hash": result.replayed_hash,
            "diff": [
                {"field": d.field, "recorded": d.recorded, "replayed": d.replayed}
                for d in result.diff
            ],
            "detail": result.detail,
        }

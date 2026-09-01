"""System endpoints — API-070 … API-076.

`/health` must answer without touching the database or Alpaca (OPS-021): it is
the container's liveness probe, and a probe that depends on a dependency
restarts the container every time that dependency blinks.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from underwriter import __version__
from underwriter.audit.ledger import Actor, append
from underwriter.data.credentials import has_credentials, load_data_credentials
from underwriter.db import session_scope
from underwriter.db.models import SchedulerRun, SystemConfig
from underwriter.kernel.limits import DEFAULT_LIMITS

BOOT_TIME = time.monotonic()


def health() -> dict[str, Any]:
    """API-070 — liveness. Always 200 while the process is up."""
    return {
        "status": "ok",
        "version": __version__,
        "as_of": datetime.now(UTC).isoformat(),
    }


def _alpaca_check() -> dict[str, Any]:
    """ALP-004: report whether the read path has its own credentials.

    A shared account key still works, but the isolation the SRS asks for is
    absent, and a degradation nobody can see is a degradation nobody fixes.
    """
    if not has_credentials():
        return {
            "status": "not_configured",
            "detail": "no ALPACA_API_KEY / ALPACA_SECRET_KEY (ROAD-D0-02)",
        }

    credentials = load_data_credentials()
    if credentials.is_dedicated_data_key:
        return {"status": "ok", "detail": "dedicated read-only data key (ALP-004)"}

    return {
        "status": "degraded",
        "detail": "using the shared account key; a dedicated data key is preferred (ALP-004)",
    }


def health_deep() -> tuple[dict[str, Any], int]:
    """API-071 — readiness, per dependency.

    Returns 503 with the per-dependency map when anything critical is down.
    OPS-021: a readiness failure must NOT restart the container, only mark it
    degraded, because a restart mid-cycle aborts work that is already in flight.
    """
    checks: dict[str, Any] = {
        "database": {"status": "not_configured", "detail": "SQLite layer not built yet"},
        "alpaca_rest": _alpaca_check(),
        "mcp": {"status": "not_configured", "detail": "subprocess not supervised yet"},
        "llm": {
            "status": "configured" if os.environ.get("GROQ_API_KEY") else "not_configured",
            "detail": os.environ.get("GROQ_MODEL", "GROQ_MODEL unset"),
        },
        "scheduler": {"status": "not_configured", "detail": "APScheduler not started yet"},
        "kernel": {"status": "ok", "detail": "rule table loaded"},
    }
    healthy = all(c["status"] == "ok" for c in checks.values())
    body = {
        "status": "ok" if healthy else "degraded",
        "as_of": datetime.now(UTC).isoformat(),
        "checks": checks,
    }
    return body, 200 if healthy else 503


def status() -> dict[str, Any]:
    """API-072 — mode, kill switch, profile, uptime, version."""
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "version": __version__,
        "environment": os.environ.get("ENVIRONMENT", "local"),
        "app_version": os.environ.get("APP_VERSION", "dev"),
        "uptime_sec": round(time.monotonic() - BOOT_TIME, 1),
        **_live_state(),
    }


def _live_state() -> dict[str, Any]:
    """Mode, kill switch and the last cycle, read from the book."""
    from underwriter.server import running_scheduler

    with session_scope() as session:
        config = session.get(SystemConfig, 1)
        last = session.execute(
            select(SchedulerRun).order_by(SchedulerRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()

        scheduler = running_scheduler()

        return {
            "mode": config.mode if config else "MANAGE_ONLY",
            "kill_switch": bool(config.kill_switch) if config else False,
            "strategy_profile": (
                config.strategy_profile
                if config
                else os.environ.get("STRATEGY_PROFILE", "PERFORMANCE")
            ),
            "paper_trading": os.environ.get("ALPACA_PAPER_TRADE", "true"),
            "last_cycle": (
                {
                    "job": last.job_name,
                    "status": last.status,
                    "outcome": last.outcome,
                    "at": last.started_at.isoformat(),
                    "duration_ms": last.duration_ms,
                }
                if last
                else None
            ),
            "scheduler": scheduler.status() if scheduler else {"running": False, "jobs": []},
            "note": (
                "The desk boots in MANAGE_ONLY every time (ERR-007). Entries need an "
                "operator to promote it to ACTIVE after reconciliation has run."
                if (config.mode if config else "MANAGE_ONLY") != "ACTIVE"
                else None
            ),
        }


def set_mode(mode: str, actor: str = "OPERATOR") -> dict[str, Any]:
    """API-073 — set the system mode.

    SEC-012: this changes who may *ask*, never what is allowed. A desk in
    ACTIVE still has every proposal adjudicated by the same 25 rules, and
    promoting the mode buys no privilege with the Kernel.
    """
    with session_scope() as session:
        config = session.get(SystemConfig, 1)
        if config is None:
            config = SystemConfig(id=1, mode=mode)
            session.add(config)
            before = None
        else:
            before = {"mode": config.mode}
            config.mode = mode

        config.updated_at = datetime.now(UTC)
        config.updated_by = actor

        append(
            session,
            actor=Actor.OPERATOR,
            action="MODE_CHANGED",
            entity_type="system_config",
            entity_id="1",
            before=before,
            after={"mode": mode},
        )

        return {"as_of": datetime.now(UTC).isoformat(), "mode": mode, "actor": actor}


def set_kill_switch(engaged: bool, actor: str = "OPERATOR") -> dict[str, Any]:
    """API-074 — engage or release the kill switch.

    Takes effect on the next cycle: SK-023 reads it from the book on every
    adjudication, so nothing in flight can outrun it by more than one tick.
    """
    with session_scope() as session:
        config = session.get(SystemConfig, 1)
        if config is None:
            config = SystemConfig(id=1, mode="MANAGE_ONLY", kill_switch=1 if engaged else 0)
            session.add(config)
            before = None
        else:
            before = {"kill_switch": bool(config.kill_switch)}
            config.kill_switch = 1 if engaged else 0

        config.updated_at = datetime.now(UTC)
        config.updated_by = actor

        append(
            session,
            actor=Actor.OPERATOR,
            action="KILL_SWITCH_ENGAGED" if engaged else "KILL_SWITCH_RELEASED",
            entity_type="system_config",
            entity_id="1",
            before=before,
            after={"kill_switch": engaged},
        )

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "kill_switch": engaged,
            "actor": actor,
            "effective": "next scheduler cycle",
        }


def scheduler_runs(job: str | None, limit: int) -> dict[str, Any]:
    """API-075 — scheduler history, newest first.

    NO_ACTION rows are the majority and that is correct: most cycles decline to
    trade, and FR-026 calls those successful.
    """
    with session_scope() as session:
        query = select(SchedulerRun).order_by(SchedulerRun.started_at.desc()).limit(limit)
        if job:
            query = query.where(SchedulerRun.job_name == job)

        rows = session.execute(query).scalars().all()
        return {
            "as_of": datetime.now(UTC).isoformat(),
            "runs": [
                {
                    "id": row.id,
                    "job": row.job_name,
                    "correlation_id": row.correlation_id,
                    "status": row.status,
                    "outcome": row.outcome,
                    "started_at": row.started_at.isoformat(),
                    "duration_ms": row.duration_ms,
                    "error": row.error_message,
                }
                for row in rows
            ],
            "returned": len(rows),
            "empty": not rows,
        }


def config() -> dict[str, Any]:
    """API-076 — the redacted effective config.

    Limits and parameters only. Nothing from the environment that could be a
    secret is read here at all, so there is no redaction list to keep correct.
    """
    limits = DEFAULT_LIMITS
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "strategy_profile": os.environ.get("STRATEGY_PROFILE", "PERFORMANCE"),
        "kernel": {
            "max_deployed_pct": str(limits.max_deployed_pct),
            "max_position_loss_pct": str(limits.max_position_loss_pct),
            "max_open_policies": limits.max_open_policies,
            "max_underlying_concentration": str(limits.max_underlying_concentration),
            "max_portfolio_risk_pct": str(limits.max_portfolio_risk_pct),
            "max_daily_loss_pct": str(limits.max_daily_loss_pct),
            "max_drawdown_pct": str(limits.max_drawdown_pct),
            "min_dte_at_entry": limits.min_dte_at_entry,
            "verdict_ttl_sec": limits.verdict_ttl_sec,
        },
        "honesty_statement": (
            "Credit spreads win often and lose occasionally by a larger amount. "
            "Four sessions is not a statistically meaningful sample and no edge is "
            "claimed. What is guaranteed is a bounded, pre-computed maximum loss, "
            "enforced before any order is transmitted."
        ),
    }

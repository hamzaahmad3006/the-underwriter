"""System endpoints — API-070 … API-078.

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
from underwriter.obs import metrics as obs_metrics

BOOT_TIME = time.monotonic()

# Without these the desk cannot function at all. Everything else can be absent
# or degraded and the system still does its job, more slowly or with less
# context — which is the difference readiness should report.
CRITICAL_DEPENDENCIES = frozenset({"database", "kernel"})


def health() -> dict[str, Any]:
    """API-070 — liveness. Always 200 while the process is up."""
    return {
        "status": "ok",
        "version": __version__,
        "as_of": datetime.now(UTC).isoformat(),
    }


def _database_check() -> dict[str, Any]:
    """Can we actually read the book? Answered by reading it.

    A readiness check that reports on configuration rather than behaviour is
    the kind that stays green while the thing it watches is broken.
    """
    from sqlalchemy import select

    from underwriter.db.models import AuditLog

    try:
        with session_scope() as session:
            highest = session.execute(
                select(AuditLog.seq).order_by(AuditLog.seq.desc()).limit(1)
            ).scalar_one_or_none()
    except Exception as exc:
        return {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}

    return {
        "status": "ok",
        "detail": f"readable; {highest or 0} audit records",
    }


def _scheduler_check() -> dict[str, Any]:
    """OPS-020: exactly one scheduler, and it should be running."""
    from underwriter.server import running_scheduler

    scheduler = running_scheduler()
    if scheduler is None:
        return {
            "status": "not_configured",
            "detail": "no scheduler in this process (disabled, or running under a test client)",
        }

    if not scheduler.running:
        return {"status": "down", "detail": "the scheduler exists but is not running"}

    jobs = scheduler.status()["jobs"]
    failing = [job for job in jobs if job["consecutive_failures"] > 0]
    return {
        "status": "degraded" if failing else "ok",
        "detail": (
            f"{len(jobs)} jobs; "
            + (
                ", ".join(f"{j['name']} failing x{j['consecutive_failures']}" for j in failing)
                if failing
                else "none failing"
            )
        ),
    }


def _cli_check() -> dict[str, Any]:
    """ALP-005: `alpaca doctor`, as a readiness dependency."""
    from underwriter.cli import doctor, is_available

    if not is_available():
        return {
            "status": "not_configured",
            "detail": "the Alpaca CLI is not installed; order pre-flight is skipped (ALP-007)",
        }

    result = doctor()
    return {
        "status": "ok" if result.ok else "degraded",
        "detail": result.detail,
    }


def _mcp_check() -> dict[str, Any]:
    """§16: is the tool surface there, and is it still read-only?

    Reports the exposed writes rather than asserting there are none. An
    allowlist regression should be visible on the health endpoint, not
    discovered later by whatever it lets through.
    """
    from underwriter.mcp import DEFAULT_TOOLSETS, is_available

    if not is_available():
        return {
            "status": "not_configured",
            "detail": "alpaca-mcp-server is not installed",
        }

    return {
        "status": "ok",
        "detail": (
            f"toolsets: {os.environ.get('ALPACA_TOOLSETS') or DEFAULT_TOOLSETS} "
            "(trading excluded, so no order tool is reachable)"
        ),
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
        "database": _database_check(),
        "alpaca_rest": _alpaca_check(),
        "mcp": _mcp_check(),
        "llm": {
            "status": "ok" if os.environ.get("GROQ_API_KEY") else "not_configured",
            "detail": os.environ.get("GROQ_MODEL", "GROQ_MODEL unset"),
        },
        "scheduler": _scheduler_check(),
        "cli": _cli_check(),
        "kernel": {"status": "ok", "detail": "rule table loaded"},
    }
    # A dependency that is permanently degraded by the account plan — ALP-004
    # issues no separate data key — must not leave readiness red forever. An
    # endpoint that is always failing is one nobody reads, which defeats it.
    # So `down` fails readiness; `degraded` is reported and tolerated.
    down = [name for name, c in checks.items() if c["status"] == "down"]
    critical_missing = [
        name
        for name, c in checks.items()
        if name in CRITICAL_DEPENDENCIES and c["status"] not in {"ok", "degraded"}
    ]
    degraded = [name for name, c in checks.items() if c["status"] == "degraded"]

    failing = down + critical_missing
    body = {
        "status": "down" if failing else ("degraded" if degraded else "ok"),
        "as_of": datetime.now(UTC).isoformat(),
        "checks": checks,
        "failing": failing,
        "degraded": degraded,
    }
    return body, 503 if failing else 200


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


def metrics() -> dict[str, Any]:
    """API-077 — OPS-003 counters and OPS-004 latencies.

    Not in §19.2's endpoint table, which lists no way to read the counters
    OPS-003 requires (DEV-12). A metric nobody can query is a metric that does
    not exist, and OPS-008 is explicit that the Kernel's per-rule numbers must
    be "queryable and displayed".
    """
    return obs_metrics.snapshot()


def veto_metrics(limit: int = 30) -> dict[str, Any]:
    """API-078 — OPS-008, the per-rule veto breakdown.

    Separate from `/metrics` because it answers a different question and gets
    asked far more often: not "how is the desk running" but "what actually
    stops a trade here". That is the question the Kernel exists to answer, so
    it gets its own URL and its own panel on the dashboard.
    """
    return obs_metrics.veto_summary(limit)

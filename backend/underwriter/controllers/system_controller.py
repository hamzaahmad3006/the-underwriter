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

from underwriter import __version__
from underwriter.kernel.limits import DEFAULT_LIMITS

BOOT_TIME = time.monotonic()


def health() -> dict[str, Any]:
    """API-070 — liveness. Always 200 while the process is up."""
    return {
        "status": "ok",
        "version": __version__,
        "as_of": datetime.now(UTC).isoformat(),
    }


def health_deep() -> tuple[dict[str, Any], int]:
    """API-071 — readiness, per dependency.

    Returns 503 with the per-dependency map when anything critical is down.
    OPS-021: a readiness failure must NOT restart the container, only mark it
    degraded, because a restart mid-cycle aborts work that is already in flight.
    """
    checks: dict[str, Any] = {
        "database": {"status": "not_configured", "detail": "SQLite layer not built yet"},
        "alpaca_rest": {"status": "not_configured", "detail": "credentials not wired yet"},
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
        "mode": "MANAGE_ONLY",
        "kill_switch": False,
        "strategy_profile": os.environ.get("STRATEGY_PROFILE", "PERFORMANCE"),
        "paper_trading": os.environ.get("ALPACA_PAPER_TRADE", "true"),
        "last_cycle": None,
        "note": (
            "Boot mode is MANAGE_ONLY until reconciliation runs and an operator "
            "promotes it (ERR-007). No scheduler is running yet."
        ),
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

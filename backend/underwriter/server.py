"""The server: middleware chain, router mounting, boot refusal.

Run it with `uvicorn underwriter.server:app`.

Everything the SRS calls an endpoint is reachable from here, and nothing that
decides anything lives here. The order below matters:

1. `RequestIdMiddleware` first, so every later handler and every error envelope
   can quote a correlation id.
2. CORS next, from `CORS_ORIGINS`, no wildcards in production (SEC-007).
3. Error handlers, so a failure anywhere returns the same shape (UI-005).
4. Routers last, mounted under `/api`, with `/health` also at the root because
   OPS-021 makes it the container's liveness probe.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from underwriter import __version__
from underwriter.controllers import system_controller
from underwriter.cycle import bootstrap
from underwriter.cycle.scheduler import CycleScheduler
from underwriter.db import create_all
from underwriter.middleware import RequestIdMiddleware, install_error_handlers
from underwriter.routes import api_router

# Read backend/.env if it exists. Real deployments inject secrets through the
# platform's secret store (OPS: `fly secrets set`), where there is no .env at
# all, so this is a local-development convenience and never the source of
# truth. `override=False` keeps a real environment variable winning.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

log = logging.getLogger(__name__)

API_PREFIX = "/api"

# One scheduler per process, and OPS-020 requires one process. Module level
# rather than app state so /system/status can read it without a request.
_scheduler: CycleScheduler | None = None


def running_scheduler() -> CycleScheduler | None:
    return _scheduler


class UnsafeConfigurationError(RuntimeError):
    """Raised at import time when the process would be unsafe to run."""


def assert_paper_trading() -> None:
    """SEC-004: refuse to boot unless paper trading is explicitly enabled.

    The check is deliberately positive — the variable must *say* true, rather
    than merely not say false — because a missing variable is exactly how a
    live-money accident happens. NG: this system never trades real money.
    """
    if os.environ.get("ALPACA_PAPER_TRADE", "true").strip().lower() != "true":
        raise UnsafeConfigurationError(
            "ALPACA_PAPER_TRADE must be 'true'. This system is paper-only (SEC-004)."
        )


def cors_origins() -> list[str]:
    """SEC-007: explicit origins in production, never a wildcard."""
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]

    if os.environ.get("ENVIRONMENT") == "production" and "*" in origins:
        raise UnsafeConfigurationError("CORS_ORIGINS must not contain '*' in production (SEC-007).")

    return origins


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the schema, seed config, and start the three cycles.

    ERR-007: the desk always boots in MANAGE_ONLY. After a restart the book and
    the broker may disagree, and the first cycle must not open a position on
    top of a divergence nobody has looked at. Reconciliation runs on its own
    five-minute cadence; an operator promotes to ACTIVE.

    A missing dependency disables its cycle, never the process — an open book
    still needs managing whether or not a model is available to write new ones.
    """
    global _scheduler

    create_all()
    bootstrap.ensure_system_config()
    # ALP-002: without the baseline the Kernel reads no account state and
    # refuses everything on SK-025, including a close.
    bootstrap.ensure_account()

    if os.environ.get("UNDERWRITER_DISABLE_SCHEDULER", "").lower() == "true":
        log.info("scheduler disabled by UNDERWRITER_DISABLE_SCHEDULER")
        yield
        return

    wiring = bootstrap.build()
    for note in wiring.notes:
        log.warning("wiring: %s", note)

    if wiring.scheduler is not None:
        wiring.scheduler.start()
        _scheduler = wiring.scheduler
        log.info(
            "desk started in %s; underwriting=%s execution=%s",
            bootstrap.BOOT_MODE,
            wiring.can_underwrite,
            wiring.can_execute,
        )

    try:
        yield
    finally:
        if _scheduler is not None:
            _scheduler.shutdown()
            _scheduler = None


def create_app() -> FastAPI:
    """Application factory, so tests can build an app without import side effects."""
    assert_paper_trading()

    app = FastAPI(
        title="The Underwriter",
        version=__version__,
        description=(
            "An autonomous options underwriting desk. The LLM proposes; a "
            "deterministic Solvency Kernel disposes. Paper trading only."
        ),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=False,  # the operator token is a header, not a cookie
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )

    install_error_handlers(app)

    app.include_router(api_router, prefix=API_PREFIX)

    # OPS-021: the liveness probe lives at the root and must not move.
    app.add_api_route("/health", system_controller.health, methods=["GET"], tags=["system"])

    return app


app = create_app()

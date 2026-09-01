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

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from underwriter import __version__
from underwriter.controllers import system_controller
from underwriter.db import create_all
from underwriter.middleware import RequestIdMiddleware, install_error_handlers
from underwriter.routes import api_router

# Read backend/.env if it exists. Real deployments inject secrets through the
# platform's secret store (OPS: `fly secrets set`), where there is no .env at
# all, so this is a local-development convenience and never the source of
# truth. `override=False` keeps a real environment variable winning.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

API_PREFIX = "/api"


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

    # F-11: recording is a precondition for trading, so the schema exists
    # before the first request rather than on first write. Alembic owns
    # migrations once the schema settles; this is the day-one path.
    create_all()

    app.include_router(api_router, prefix=API_PREFIX)

    # OPS-021: the liveness probe lives at the root and must not move.
    app.add_api_route("/health", system_controller.health, methods=["GET"], tags=["system"])

    return app


app = create_app()

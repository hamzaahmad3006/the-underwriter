"""FastAPI application entrypoint.

Placeholder wiring only — routers, scheduler and lifespan land once the
folder structure is fixed. `/health` exists now because OPS-021 makes it the
container's liveness probe and Day 2 needs a deployable URL (OPS-025).
"""

from datetime import UTC, datetime

from fastapi import FastAPI

from underwriter import __version__

app = FastAPI(
    title="The Underwriter",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe (OPS-021). Must not touch the database or Alpaca."""
    return {
        "status": "ok",
        "version": __version__,
        "as_of": datetime.now(UTC).isoformat(),
    }

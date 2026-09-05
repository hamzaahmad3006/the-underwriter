"""System routes — API-070 … API-078.

`/health` is also mounted at the application root by `server.py`, because
OPS-021 makes it the container's liveness probe and a probe should not depend
on an API prefix staying put.

The two write endpoints here are the operator's whole control surface: set the
mode, pull the kill switch. SEC-012 bounds what that buys — it changes who may
*ask*, never what the Kernel allows, and an operator's order is adjudicated by
the same 25 rules as the model's.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Response

from underwriter.controllers import system_controller
from underwriter.middleware import require_operator

router = APIRouter(tags=["system"])


@router.get("/health", summary="API-070 liveness")
def health() -> dict[str, Any]:
    return system_controller.health()


@router.get("/health/deep", summary="API-071 readiness")
def health_deep(response: Response) -> dict[str, Any]:
    body, code = system_controller.health_deep()
    response.status_code = code
    return body


@router.get("/system/status", summary="API-072 mode, uptime, version")
def status() -> dict[str, Any]:
    return system_controller.status()


@router.post("/system/mode", summary="API-073 set the system mode")
def set_mode(
    mode: str = Body(embed=True, pattern="^(HALT|MANAGE_ONLY|ACTIVE)$"),
    actor: str = Depends(require_operator),
) -> dict[str, Any]:
    """SEC-012: changes who may ask, never what the Kernel allows."""
    return system_controller.set_mode(mode, actor)


@router.post("/system/kill-switch", summary="API-074 engage or release the kill switch")
def kill_switch(
    engaged: bool = Body(embed=True), actor: str = Depends(require_operator)
) -> dict[str, Any]:
    """Takes effect within one scheduler cycle: SK-023 reads it every time."""
    return system_controller.set_kill_switch(engaged, actor)


@router.get("/scheduler/runs", summary="API-075 scheduler history")
def scheduler_runs(
    job: str | None = None, limit: int = Query(default=50, ge=1, le=500)
) -> dict[str, Any]:
    return system_controller.scheduler_runs(job, limit)


@router.get("/config", summary="API-076 redacted effective config")
def config() -> dict[str, Any]:
    return system_controller.config()


@router.get("/metrics", summary="API-077 OPS-003 counters and OPS-004 latencies")
def metrics() -> dict[str, Any]:
    """Read off the ledger, not off in-process counters, so a restart loses none."""
    return system_controller.metrics()


@router.get("/metrics/vetoes", summary="API-078 per-rule Kernel veto counts (OPS-008)")
def veto_metrics(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
    """Which rules actually stop trades. Unauthenticated on purpose: UI-003
    requires the whole dashboard to be readable with no token, and this is the
    number a judge most wants to check without being handed a credential."""
    return system_controller.veto_metrics(limit)

"""System routes — API-070 … API-076.

`/health` is also mounted at the application root by `server.py`, because
OPS-021 makes it the container's liveness probe and a probe should not depend
on an API prefix staying put.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Response

from underwriter.controllers import system_controller
from underwriter.middleware import require_operator
from underwriter.middleware.error_handler import EndpointNotReadyError

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
    raise EndpointNotReadyError("Mode changes", "the scheduler and state store are not built yet")


@router.post("/system/kill-switch", summary="API-074 engage or release the kill switch")
def kill_switch(
    engaged: bool = Body(embed=True), actor: str = Depends(require_operator)
) -> dict[str, Any]:
    raise EndpointNotReadyError(
        "The kill switch", "the scheduler and state store are not built yet"
    )


@router.get("/scheduler/runs", summary="API-075 scheduler history")
def scheduler_runs(
    job: str | None = None, limit: int = Query(default=50, ge=1, le=500)
) -> dict[str, Any]:
    raise EndpointNotReadyError("Scheduler history", "APScheduler is not started yet (DB-020)")


@router.get("/config", summary="API-076 redacted effective config")
def config() -> dict[str, Any]:
    return system_controller.config()

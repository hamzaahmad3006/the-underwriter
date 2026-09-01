"""Dashboard routes — API-010 … API-012. All read, no auth (UI-003)."""

from typing import Any

from fastapi import APIRouter, Query

from underwriter.controllers import dashboard_controller

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview", summary="API-010 executive summary")
def overview() -> dict[str, Any]:
    return dashboard_controller.overview()


@router.get("/equity-curve", summary="API-011 equity time series")
def equity_curve(
    range_: str = Query(default="1d", alias="range", pattern="^(1d|all)$"),
) -> dict[str, Any]:
    return dashboard_controller.equity_series(range_)


@router.get("/stats", summary="API-012 performance statistics")
def stats() -> dict[str, Any]:
    return dashboard_controller.stats()

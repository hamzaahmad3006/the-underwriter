"""Risk routes — API-040 … API-042.

API-041 is live already: the limit table comes straight off the Kernel, so the
dashboard shows the rules actually in force rather than a hand-kept copy.
"""

from typing import Any

from fastapi import APIRouter, Query

from underwriter.controllers import kernel_controller, risk_controller

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/exposure", summary="API-040 portfolio exposure and headroom")
def exposure() -> dict[str, Any]:
    return risk_controller.exposure()


@router.get("/limits", summary="API-041 the active rule table")
def limits() -> dict[str, Any]:
    return kernel_controller.rule_table()


@router.get("/events", summary="API-042 risk event feed")
def events(
    severity: str | None = Query(default=None, pattern="^(INFO|WARNING|CRITICAL)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    return risk_controller.events(severity, limit)

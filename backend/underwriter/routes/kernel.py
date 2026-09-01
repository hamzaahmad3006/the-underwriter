"""Kernel routes — API-043 … API-045.

`/kernel/simulate` is the demo weapon and it is live now. It runs the real
`kernel.evaluate()` and returns the full 26-rule breakdown. It has no execution
engine behind it, so `executed` is false by construction rather than by flag.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query

from underwriter.controllers import kernel_controller
from underwriter.controllers.kernel_controller import SimulateRequest
from underwriter.middleware import require_operator

router = APIRouter(prefix="/kernel", tags=["kernel"])


@router.get("/decisions", summary="API-043 the Kernel Veto Feed")
def decisions(
    verdict: str | None = Query(default=None, pattern="^(APPROVE|REJECT)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    return kernel_controller.list_decisions(verdict, limit)


@router.get("/decisions/{decision_id}", summary="API-044 one verdict in full")
def decision(decision_id: str) -> dict[str, Any]:
    return kernel_controller.get_decision(decision_id)


@router.post("/simulate", summary="API-045 adjudicate without executing")
def simulate(body: SimulateRequest, actor: str = Depends(require_operator)) -> dict[str, Any]:
    return kernel_controller.simulate(body)

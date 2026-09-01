"""Policy routes — API-020 … API-022.

API-022 is the only write here, and it *requests* a close. FR-066 sends it
through the Kernel like any other action; the operator has no privileged path.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from underwriter.controllers import policies_controller
from underwriter.middleware import require_operator

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", summary="API-020 the underwriting book")
def list_policies(
    status: str | None = None,
    underlying: str | None = Query(default=None, pattern="^[A-Z]{1,6}$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return policies_controller.list_policies(status, underlying, limit, offset)


@router.get("/{policy_id}", summary="API-021 full policy detail")
def get_policy(policy_id: str) -> dict[str, Any]:
    return policies_controller.get_policy(policy_id)


@router.post("/{policy_id}/close", status_code=202, summary="API-022 request a close")
def request_close(
    policy_id: str,
    reason: str = Body(default="operator_request", embed=True),
    actor: str = Depends(require_operator),
) -> dict[str, Any]:
    return policies_controller.request_close(policy_id, reason)

"""Audit routes — API-060 … API-062. All read: judges need no token."""

from typing import Any

from fastapi import APIRouter, Query

from underwriter.controllers import audit_controller

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/log", summary="API-060 the audit trail")
def log(
    correlation_id: str | None = None,
    actor: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    return audit_controller.log(correlation_id, actor, limit)


@router.get("/verify", summary="API-061 walk the hash chain")
def verify() -> dict[str, Any]:
    return audit_controller.verify()


@router.get("/export", summary="API-062 full ledger export")
def export(
    format_: str = Query(default="json", alias="format", pattern="^(json|csv)$"),
) -> dict[str, Any]:
    return audit_controller.export(format_)

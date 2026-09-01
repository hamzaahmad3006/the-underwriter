"""Underwriting routes — API-030 … API-033."""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from underwriter.controllers import underwriting_controller
from underwriter.middleware import require_operator

router = APIRouter(prefix="/underwriting", tags=["underwriting"])


@router.get("/candidates", summary="API-030 Actuary-priced candidates")
def candidates(correlation_id: str | None = None) -> dict[str, Any]:
    return underwriting_controller.candidates(correlation_id)


@router.get("/decisions", summary="API-031 LLM decisions with rationale")
def decisions(
    limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)
) -> dict[str, Any]:
    return underwriting_controller.decisions(limit, offset)


@router.post("/run", summary="API-032 trigger a cycle now")
def run_cycle(
    dry_run: bool = Body(default=True),
    force_underlying: str | None = Body(default=None),
    actor: str = Depends(require_operator),
) -> dict[str, Any]:
    """`dry_run=true` runs to a verdict and transmits nothing. That is the
    default here on purpose: the safe demo trigger should be the easy one."""
    return underwriting_controller.run_cycle(dry_run, force_underlying)


@router.get("/replay/{decision_id}", summary="API-033 determinism proof")
def replay(decision_id: str) -> dict[str, Any]:
    return underwriting_controller.replay(decision_id)

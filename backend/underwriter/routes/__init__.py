"""Routers — path, method and auth only.

Every router here is deliberately thin. It binds a path to a controller
function and declares whether the SRS marks that endpoint Auth = Yes. No
business logic lives in this package, which is why the controllers stay
testable without spinning up an app.

`api_router` is what `server.py` mounts.
"""

from fastapi import APIRouter

from underwriter.routes import (
    audit,
    dashboard,
    kernel,
    orders,
    policies,
    risk,
    system,
    underwriting,
)

api_router = APIRouter()
api_router.include_router(dashboard.router)
api_router.include_router(policies.router)
api_router.include_router(underwriting.router)
api_router.include_router(risk.router)
api_router.include_router(kernel.router)
api_router.include_router(orders.router)
api_router.include_router(audit.router)
api_router.include_router(system.router)

__all__ = ["api_router"]

"""One error envelope for the whole API.

UI-005 requires errors to be inline and specific, which the frontend can only
do if every failure arrives in the same shape with a machine-readable code.

`EndpointNotReadyError` exists because this system is being built against a frozen
spec in five days. An endpoint whose data layer does not exist yet returns 503
with the reason, and the dashboard renders the SRS's explain-why empty state
(UI-006). It never returns invented numbers: fabricated P&L in a trading system
is worse than a visible gap.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from underwriter.kernel.verdict import UnauthorizedExecution


class EndpointNotReadyError(RuntimeError):
    """The endpoint is specified and routed, but its data source is not built."""

    def __init__(self, what: str, blocked_on: str) -> None:
        super().__init__(f"{what} is not available yet: {blocked_on}")
        self.what = what
        self.blocked_on = blocked_on


def _envelope(
    request: Request, code: str, message: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message},
        "correlation_id": getattr(request.state, "correlation_id", None),
    }
    if extra:
        body["error"].update(extra)
    return body


def install_error_handlers(app: FastAPI) -> None:
    """Mount every handler. Called once from `server.py`."""

    @app.exception_handler(EndpointNotReadyError)
    async def _not_yet(request: Request, exc: EndpointNotReadyError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_envelope(
                request,
                "NOT_YET_IMPLEMENTED",
                str(exc),
                {"what": exc.what, "blocked_on": exc.blocked_on},
            ),
        )

    @app.exception_handler(UnauthorizedExecution)
    async def _unauthorized_execution(request: Request, exc: UnauthorizedExecution) -> JSONResponse:
        """Reaching here means something tried to execute without a verdict.

        That is not a routine 4xx. It is the Kernel's central claim being
        exercised, so it is logged loudly and answered with a refusal.
        """
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_envelope(request, "UNAUTHORIZED_EXECUTION", str(exc)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """API-004: field-level detail, always 422."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_envelope(
                request,
                "VALIDATION_FAILED",
                "Request body failed validation.",
                {"fields": exc.errors()},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(request, f"HTTP_{exc.status_code}", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Never leak internals to the client; the log keeps the detail."""
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                request,
                "INTERNAL_ERROR",
                f"{type(exc).__name__} while handling the request.",
            ),
        )

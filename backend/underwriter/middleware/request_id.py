"""Correlation id on every request.

FR-045 and OBS: one id ties a request to the cycle it triggered, the decisions
that cycle made and the orders those decisions produced. Without it the audit
log is a pile of rows that happen to share a timestamp.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Accept an inbound correlation id, or mint one, and echo it back."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or f"req_{uuid.uuid4().hex[:16]}"
        request.state.correlation_id = correlation_id

        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response

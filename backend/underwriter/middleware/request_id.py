"""Correlation id on every request.

FR-045 and OPS-002: one id ties a request to the cycle it triggered, the
decisions that cycle made and the orders those decisions produced. Without it
the audit log is a pile of rows that happen to share a timestamp.

`request.state` carries it to handlers that ask; the context variable carries it
to the ones that do not. Threading an argument through six layers so a logging
call at the bottom can quote an id would put the id in signatures that have no
other use for it.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from underwriter.obs.logging import correlation

log = logging.getLogger(__name__)

CORRELATION_HEADER = "X-Correlation-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Accept an inbound correlation id, or mint one, and echo it back."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or f"req_{uuid.uuid4().hex[:16]}"
        request.state.correlation_id = correlation_id

        started = time.perf_counter()

        with correlation(correlation_id):
            response = await call_next(request)

            # Our own access line rather than uvicorn's, which is written
            # outside this context and so always reports a null id.
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

        response.headers[CORRELATION_HEADER] = correlation_id
        return response

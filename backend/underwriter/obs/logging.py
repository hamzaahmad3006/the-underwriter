"""Structured logging — OPS-001, OPS-002, OPS-010.

Every line is JSON with `timestamp`, `level`, `component`, `correlation_id` and
`event`. No unstructured prints anywhere in the system.

The correlation id is the reason this exists as a module rather than a
`basicConfig` call. OPS-002 wants one id threaded through every log line, DB row
and API response for a cycle, and passing it explicitly through six layers would
mean every function that logs takes an argument it does not otherwise need. A
context variable carries it instead: the scheduler sets it once per cycle, the
API middleware sets it once per request, and everything underneath is unaware.

Until this existed the application logged nothing at all. Uvicorn configures its
own loggers and leaves the root alone, so `log.error("forced MANAGE_ONLY")` —
which is what F-19 and F-25 emit when the desk halts itself — went nowhere.
A halt nobody can see is a halt nobody responds to.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# OPS-002. Set once per cycle or per request; read by every line underneath.
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Keys the formatter writes itself. Anything else in `extra` is event-specific
# and passes through untouched.
RESERVED = frozenset(
    {
        "args",
        # Uvicorn attaches an ANSI-escaped duplicate of the message. In a JSON
        # line it is noise wrapped in terminal escapes.
        "color_message",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


@contextmanager
def correlation(value: str) -> Iterator[str]:
    """Scope a correlation id to one cycle or request."""
    token = _correlation_id.set(value)
    try:
        yield value
    finally:
        _correlation_id.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the OPS-001 fields always present."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            # The module path is the component. `underwriter.kernel.kernel`
            # says more about where a line came from than a hand-set label
            # that drifts from the file it lives in.
            "component": record.name,
            "correlation_id": current_correlation_id(),
            "event": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            # OPS-010: stack traces to logs, never to API responses.
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": "".join(traceback.format_exception(*record.exc_info)),
            }

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str | None = None) -> None:
    """Install the JSON formatter on the root logger, once.

    Uvicorn's own loggers are re-pointed at the same handler so access lines
    and application lines land in one stream — a platform log viewer shows one
    file, and two formats in it is one format too many.
    """
    resolved = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # Uvicorn's access line is written by the protocol handler, outside the
    # context the request middleware established, so it always reports a null
    # correlation id. `RequestIdMiddleware` logs its own line instead — same
    # information, plus the id and the duration — and this silences the
    # duplicate rather than shipping two access logs per request.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False

    # These are chatty at INFO and say nothing the audit log does not.
    for noisy in (
        "httpx",
        "httpcore",
        "apscheduler.scheduler",
        "apscheduler.executors.default",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

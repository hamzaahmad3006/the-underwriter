"""Operator authentication for write endpoints (SEC-018).

UI-003 is the shape of this: the dashboard is fully functional read-only
without a token, so judges see everything. Only the controls are gated.

SEC-012 is the limit of it: an authenticated operator is **not** privileged for
risk purposes. Every write still enters the same pipeline and is adjudicated by
the same Kernel rules. This gate decides who may *ask*, never what is allowed.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status


def require_operator(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency for every endpoint the SRS marks Auth = Yes."""
    expected = os.environ.get("OPERATOR_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_TOKEN is not configured; write endpoints are disabled.",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operator token required. Read endpoints need no auth (UI-003).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supplied = authorization.removeprefix("Bearer ").strip()
    # Constant time: a token oracle is still an oracle even on a paper account.
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator token rejected.",
        )

    return "OPERATOR"

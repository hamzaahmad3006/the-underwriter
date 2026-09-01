"""Policy endpoints — API-020, API-021, API-022.

API-022 requests a close; it does not perform one. FR-066 routes every
state-changing action through the Kernel, including an operator's own, so
this endpoint MUST NOT ever grow a direct execution path.
"""

from __future__ import annotations

from typing import Any

from underwriter.middleware.error_handler import EndpointNotReadyError


def list_policies(
    status: str | None, underlying: str | None, limit: int, offset: int
) -> dict[str, Any]:
    """API-020 — the underwriting book."""
    raise EndpointNotReadyError(
        "The underwriting book",
        "the SQLite persistence layer is not built yet (§18)",
    )


def get_policy(policy_id: str) -> dict[str, Any]:
    """API-021 — one policy with legs, orders, fills, verdict, lifecycle."""
    raise EndpointNotReadyError(
        "Policy detail",
        "the SQLite persistence layer is not built yet (§18)",
    )


def request_close(policy_id: str, reason: str) -> dict[str, Any]:
    """API-022 — ask the Kernel to authorise a close."""
    raise EndpointNotReadyError(
        "Operator-requested closes",
        "the execution engine is not built yet (§11.5)",
    )

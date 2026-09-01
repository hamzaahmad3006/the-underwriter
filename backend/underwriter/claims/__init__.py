"""The Claims Desk — `underwriter/claims/` (§11.6).

Owns the policy after entry. Every exit it decides on is routed through the
Kernel like any other action (FR-106); nothing here executes.
"""

from underwriter.claims.desk import (
    ClaimsPolicy,
    ClaimsVerdict,
    ExitReason,
    ManagedPosition,
    evaluate,
    loss_ratio,
    realized_pnl,
)

__all__ = [
    "ClaimsPolicy",
    "ClaimsVerdict",
    "ExitReason",
    "ManagedPosition",
    "evaluate",
    "loss_ratio",
    "realized_pnl",
]

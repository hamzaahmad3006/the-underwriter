"""The Solvency Kernel — the product.

It cannot be argued with, because it does not read arguments, only numbers.
Deepest test coverage in the codebase (OPS-031: 100% line and branch).
"""

from underwriter.kernel.kernel import evaluate, explain
from underwriter.kernel.verdict import (
    Decision,
    KernelVerdict,
    NonceRegistry,
    RuleResult,
    Severity,
    UnauthorizedExecution,
    authorize,
)

__all__ = [
    "Decision",
    "KernelVerdict",
    "NonceRegistry",
    "RuleResult",
    "Severity",
    "UnauthorizedExecution",
    "authorize",
    "evaluate",
    "explain",
]

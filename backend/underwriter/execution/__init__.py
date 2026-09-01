"""The Execution Engine — `underwriter/execution/` (§11.5).

The only component that holds `ALPACA_SECRET_KEY`, and the only one that can
transmit an order. Both facts are load-bearing: §10.2's authority boundary is
this package's boundary.

Nothing here decides anything. It receives a proposal and a signed verdict,
verifies the signature against that exact proposal, and turns the result into
an `mleg` order. Without a valid verdict it raises `UnauthorizedExecution` and
transmits nothing (FR-080, TEST-030).
"""

from underwriter.execution.engine import ExecutionEngine, ExecutionResult, ExecutionStatus
from underwriter.execution.order import build_entry_order, build_exit_order

__all__ = [
    "ExecutionEngine",
    "ExecutionResult",
    "ExecutionStatus",
    "build_entry_order",
    "build_exit_order",
]

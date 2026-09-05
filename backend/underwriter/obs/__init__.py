"""Observability — structured logs (OPS-001/002/010) and metrics (OPS-003/004/008).

Two modules with opposite lifetimes: logs are written as things happen, metrics
are read back off the rows those things left behind.
"""

from underwriter.obs.logging import (
    configure_logging,
    correlation,
    current_correlation_id,
    set_correlation_id,
)
from underwriter.obs.metrics import snapshot, veto_summary

__all__ = [
    "configure_logging",
    "correlation",
    "current_correlation_id",
    "set_correlation_id",
    "snapshot",
    "veto_summary",
]

"""The agent's market context, assembled over MCP — MCP-001, MCP-005.

MCP-001 requires the model's view of the world to be genuinely MCP-derived, and
this is where that happens: account shape, open positions, the clock, and the
volatility regime all arrive through tool calls rather than the REST path.

MCP-005 draws the line on the other side. None of it reaches the Kernel. The
Kernel reads account state over REST at decision time (SK-025, FR-067), so an
MCP value that is stale, wrong, or adversarial can shape what the model *reads*
and never what the firm is *allowed to do*.

That asymmetry is the whole design in one sentence: MCP informs judgment, REST
bounds authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from underwriter.mcp.client import MCPResult, MCPUnavailableError, call_tools

# The read-only context batch. Nothing here can change the book.
# Positions are deliberately absent: `get_all_positions` lives in the
# `trading` toolset, which the allowlist excludes so that no order tool is
# reachable. Positions are a REST read regardless — TD-06 makes REST
# authoritative for reconciliation, and the book already knows what it holds.
CONTEXT_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("get_clock", {}),
    ("get_account_info", {}),
]


@dataclass(frozen=True, slots=True)
class MarketContext:
    """What MCP could tell us this cycle, and what it could not."""

    clock: Any = None
    account: Any = None
    positions: Any = None
    tools_used: tuple[str, ...] = ()
    degraded: bool = False
    detail: str = ""
    latency_ms: int = 0

    @property
    def available(self) -> bool:
        return self.clock is not None or self.account is not None

    def as_prompt_lines(self) -> tuple[str, ...]:
        """Compact lines for the underwriting prompt.

        Deliberately terse. This is context, not evidence: the authoritative
        numbers the model reasons about are the Actuary's, and a long MCP dump
        would dilute them.
        """
        lines: list[str] = []

        if isinstance(self.account, dict):
            for key in ("equity", "buying_power", "cash"):
                if key in self.account:
                    lines.append(f"{key.replace('_', ' ')}: {self.account[key]}")

        if isinstance(self.positions, list):
            lines.append(f"broker reports {len(self.positions)} open position(s)")

        if self.degraded:
            lines.append("(some market context was unavailable this cycle)")

        return tuple(lines)


def fetch_context() -> MarketContext:
    """One MCP batch per cycle. Never raises into the cycle.

    MCP failing degrades the prompt and nothing else. FR-047 already forbids
    trading without a model decision, and §16.2 marks this path as informative:
    a cycle that loses its context still has an Actuary, a Kernel and a book.
    """
    try:
        result: MCPResult = call_tools(CONTEXT_TOOLS)
    except MCPUnavailableError as exc:
        return MarketContext(degraded=True, detail=str(exc))

    return MarketContext(
        clock=result.data.get("get_clock"),
        account=result.data.get("get_account_info"),
        tools_used=tuple(call.tool for call in result.calls if call.ok),
        degraded=result.degraded,
        detail=result.detail or ("all context tools returned" if not result.degraded else ""),
        latency_ms=result.total_latency_ms,
    )

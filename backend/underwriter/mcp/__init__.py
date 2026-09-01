"""Alpaca MCP integration — `underwriter/mcp/` (§16).

TD-06: MCP is the agent's tool surface; REST is authoritative. This package
assembles the context the model reasons over. It never reaches the Kernel, and
under the configured allowlist it cannot reach an order either.
"""

from underwriter.mcp.client import (
    DEFAULT_TOOLSETS,
    MCPResult,
    MCPUnavailableError,
    call_tools,
    forbidden_tools_exposed,
    is_available,
    list_tools,
    mutating_tools_exposed,
)
from underwriter.mcp.context import MarketContext, fetch_context

__all__ = [
    "DEFAULT_TOOLSETS",
    "MCPResult",
    "MCPUnavailableError",
    "MarketContext",
    "call_tools",
    "fetch_context",
    "forbidden_tools_exposed",
    "is_available",
    "list_tools",
    "mutating_tools_exposed",
]

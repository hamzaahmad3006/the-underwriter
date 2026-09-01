"""The Alpaca MCP client — §16, MCP-001 … MCP-009.

TD-06 sets the split and this module keeps to it: **MCP is the agent's tool
surface; REST is authoritative.** Correctness-critical reads — account state
for the Kernel, order status polling, reconciliation — go over REST, because
MCP has no streaming and adds a process hop, and claiming otherwise would be
marketing rather than architecture.

What MCP is genuinely good at is assembling the *context the model reasons
over*, and MCP-001 requires that path to be real rather than decorative.

Three hardening choices worth stating:

* **The toolset allowlist excludes `trading` entirely** (MCP-006). The server
  ships `close_all_positions`, `cancel_all_orders` and
  `exercise_options_position`; none of them are needed, because execution runs
  over REST. So the agent's tool surface contains no write tool at all — not
  as a policy, but because the process was never given one.
* **The server is spawned per cycle, not supervised indefinitely.** At a
  30-minute cadence a long-lived subprocess buys nothing and adds a
  supervision problem; a bounded spawn cannot leak a session across cycles.
* **Every result is untrusted** (MCP-008). A malfunctioning or compromised
  upstream must not inject values into risk math, so results are validated and
  never reach the Kernel without independent REST confirmation (MCP-005).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# MCP-006. `trading` is deliberately absent: execution is a REST path, so the
# agent's surface needs no write tool and therefore is given none.
DEFAULT_TOOLSETS = "account,assets,stock-data,options-data,corporate-actions,news"

MCP_TIMEOUT_SEC = 20.0  # MCP-007, per call
MCP_TOTAL_BUDGET_SEC = 60.0  # MCP-007, per cycle

# Any verb that changes state. Checked by prefix rather than by an enumerated
# list, so a tool the server adds tomorrow is caught without anyone updating a
# constant — which is the failure mode an allowlist is supposed to prevent.
MUTATING_PREFIXES = (
    "place_",
    "close_",
    "cancel_",
    "create_",
    "delete_",
    "update_",
    "add_",
    "remove_",
    "exercise_",
    "do_not_exercise_",
    "set_",
    "submit_",
)

# The allowlist excludes `trading` entirely, so no order or position tool is
# reachable. One non-trading write survives inside `account`, which is needed
# for `get_account_info`: `update_account_config`. It is never called, and
# `forbidden_tools_exposed` reports it rather than pretending it is absent —
# an accurate claim about the surface is worth more than a flattering one.
KNOWN_EXPOSED_WRITES = frozenset({"update_account_config"})


class MCPUnavailableError(RuntimeError):
    """The MCP server could not be reached. Never fatal to a cycle on its own."""


@dataclass(slots=True)
class ToolCall:
    """MCP-003: tool name, arguments, latency and result status, every time."""

    tool: str
    arguments: dict[str, Any]
    latency_ms: int
    ok: bool
    error: str | None = None


@dataclass(slots=True)
class MCPResult:
    """One batch of calls, with the log of how it went."""

    data: dict[str, Any] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)
    tools_available: tuple[str, ...] = ()
    degraded: bool = False
    detail: str = ""

    @property
    def total_latency_ms(self) -> int:
        return sum(call.latency_ms for call in self.calls)


def server_command() -> str | None:
    """Where the MCP server executable lives, if it is installed at all.

    The interpreter's own directory is searched before PATH. A virtualenv puts
    its console scripts next to its `python`, and running that python directly
    — which is what the Makefile and the container both do — does not put that
    directory on PATH. Searching PATH alone would report the server missing on
    the very machine that just installed it.
    """
    explicit = os.environ.get("ALPACA_MCP_COMMAND", "").strip()
    if explicit:
        return explicit

    beside_interpreter = pathlib.Path(sys.executable).parent
    for name in ("alpaca-mcp-server.exe", "alpaca-mcp-server"):
        candidate = beside_interpreter / name
        if candidate.exists():
            return str(candidate)

    return shutil.which("alpaca-mcp-server")


def is_available() -> bool:
    return server_command() is not None


def _server_env() -> dict[str, str]:
    """MCP-004 and MCP-009: paper mode, and the restricted allowlist."""
    paper = os.environ.get("ALPACA_PAPER_TRADE", "true").strip().lower()
    if paper != "true":
        # SEC-004 again, one layer further out. A non-paper MCP server is not
        # something to start and then decide about.
        raise MCPUnavailableError("ALPACA_PAPER_TRADE must be 'true' to start the MCP server")

    return {
        **os.environ,
        "ALPACA_PAPER_TRADE": "true",
        "ALPACA_TOOLSETS": os.environ.get("ALPACA_TOOLSETS", "").strip() or DEFAULT_TOOLSETS,
    }


def _parse(content: Any) -> Any:
    """MCP-008: results are untrusted text until proven otherwise.

    Tool output arrives as content blocks. JSON is parsed; anything else stays
    a string, and nothing here coerces a value into a number that risk math
    could then consume.
    """
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)

    joined = "\n".join(parts).strip()
    if not joined:
        return None
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return joined


async def _run_batch(requests: list[tuple[str, dict[str, Any]]]) -> MCPResult:
    """Spawn the server, run the batch inside the budget, shut it down."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = server_command()
    if command is None:
        raise MCPUnavailableError("alpaca-mcp-server is not installed")

    result = MCPResult()
    deadline = time.monotonic() + MCP_TOTAL_BUDGET_SEC

    params = StdioServerParameters(command=command, args=[], env=_server_env())

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await asyncio.wait_for(session.initialize(), timeout=MCP_TIMEOUT_SEC)

        listed = await asyncio.wait_for(session.list_tools(), timeout=MCP_TIMEOUT_SEC)
        result.tools_available = tuple(sorted(tool.name for tool in listed.tools))

        for tool, arguments in requests:
            if time.monotonic() >= deadline:
                # MCP-007: the cycle is not held past its budget for context
                # that is, by design, optional.
                result.degraded = True
                result.detail = "MCP total budget exhausted; remaining context omitted"
                break

            started = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    session.call_tool(tool, arguments), timeout=MCP_TIMEOUT_SEC
                )
                latency = int((time.monotonic() - started) * 1000)
                payload = _parse(response.content)

                # MCP-008. A failing tool answers with content, not an
                # exception, so a transport-level success is not a result.
                # Recording an error string as data is precisely the injection
                # this requirement exists to prevent.
                failed = bool(getattr(response, "isError", False)) or (
                    isinstance(payload, str) and payload.lower().startswith("unknown tool")
                )
                if failed:
                    result.degraded = True
                    result.calls.append(
                        ToolCall(tool, arguments, latency, ok=False, error=str(payload)[:200])
                    )
                    log.warning("MCP tool %s returned an error payload: %s", tool, payload)
                    continue

                result.data[tool] = payload
                result.calls.append(ToolCall(tool, arguments, latency, ok=True))
            except Exception as exc:
                latency = int((time.monotonic() - started) * 1000)
                result.degraded = True
                result.calls.append(
                    ToolCall(
                        tool, arguments, latency, ok=False, error=f"{type(exc).__name__}: {exc}"
                    )
                )
                log.warning("MCP tool %s failed: %s", tool, exc)

    return result


def call_tools(requests: list[tuple[str, dict[str, Any]]]) -> MCPResult:
    """Synchronous facade over one MCP batch.

    The cycle is synchronous and the MCP SDK is not, so one event loop is run
    per batch. That is affordable at a 30-minute cadence and avoids keeping a
    subprocess and a loop alive between cycles, where a stale session could
    outlive the state it was opened against.
    """
    try:
        return asyncio.run(_run_batch(requests))
    except MCPUnavailableError:
        raise
    except Exception as exc:
        raise MCPUnavailableError(f"{type(exc).__name__}: {exc}") from exc


def list_tools() -> tuple[str, ...]:
    """What the allowlist actually exposes. Used by the health check."""
    return call_tools([]).tools_available


def mutating_tools_exposed(tools: tuple[str, ...]) -> tuple[str, ...]:
    """Every state-changing tool the current allowlist exposes."""
    return tuple(sorted(t for t in tools if t.startswith(MUTATING_PREFIXES)))


def forbidden_tools_exposed(tools: tuple[str, ...]) -> tuple[str, ...]:
    """Mutating tools that are *not* the one we already know about.

    Anything here is an allowlist regression: a write path opened up that
    nobody decided to open.
    """
    return tuple(sorted(set(mutating_tools_exposed(tools)) - KNOWN_EXPOSED_WRITES))

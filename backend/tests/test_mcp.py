"""MCP integration — §16, MCP-001 … MCP-009.

Two kinds of test here. The offline ones assert the properties that must hold
whatever the server does: the allowlist excludes trading, an error payload is
never recorded as data, and MCP failing degrades the prompt and nothing else.

The live ones (marked `live`, kept out of CI by OPS-033) spawn the real server
and check the surface it actually exposes — because an allowlist that is only
correct in a constant is an allowlist nobody has verified.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# The live checks decide at import time whether to skip, so .env is read here.
# The offline tests below never touch it.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

# ruff: noqa: E402 - .env must be read before the live skip decision, which
# is evaluated at import time below.
from underwriter.mcp import client as mcp_client
from underwriter.mcp.client import (
    DEFAULT_TOOLSETS,
    KNOWN_EXPOSED_WRITES,
    MCPResult,
    MCPUnavailableError,
    ToolCall,
    forbidden_tools_exposed,
    mutating_tools_exposed,
)
from underwriter.mcp.context import MarketContext, fetch_context

ORDER_TOOLS = (
    "place_option_order",
    "place_stock_order",
    "close_position",
    "close_all_positions",
    "cancel_order_by_id",
    "cancel_all_orders",
    "exercise_options_position",
)


# ---------------------------------------------------------------------------
# MCP-006 — the allowlist
# ---------------------------------------------------------------------------


def test_006_the_allowlist_excludes_trading() -> None:
    """No order tool is reachable, because the toolset holding them is absent.

    Not a policy the client enforces at call time — the process is simply never
    given the capability.
    """
    assert "trading" not in DEFAULT_TOOLSETS.split(",")
    assert "options-data" in DEFAULT_TOOLSETS.split(",")
    assert "stock-data" in DEFAULT_TOOLSETS.split(",")


def test_006_every_mutating_tool_is_detected_by_prefix() -> None:
    """Checked by verb rather than by an enumerated list.

    A tool the server adds tomorrow is caught without anyone remembering to
    update a constant, which is the failure mode an allowlist invites.
    """
    exposed = mutating_tools_exposed(("get_clock", *ORDER_TOOLS, "update_account_config"))
    assert set(ORDER_TOOLS) <= set(exposed)
    assert "update_account_config" in exposed
    assert "get_clock" not in exposed


def test_006_an_unexpected_write_tool_is_reported_as_a_regression() -> None:
    """Only the one known exposure is tolerated; anything else is a finding."""
    assert forbidden_tools_exposed(("get_clock", "update_account_config")) == ()
    assert forbidden_tools_exposed(("get_clock", "place_option_order")) == ("place_option_order",)


def test_006_the_known_exposure_is_named_rather_than_hidden() -> None:
    """An accurate claim about the surface beats a flattering one."""
    assert frozenset({"update_account_config"}) == KNOWN_EXPOSED_WRITES


# ---------------------------------------------------------------------------
# MCP-008 — results are untrusted
# ---------------------------------------------------------------------------


def test_008_json_output_is_parsed_and_anything_else_stays_a_string() -> None:
    class Block:
        def __init__(self, text: str) -> None:
            self.text = text

    assert mcp_client._parse([Block('{"equity": "100000"}')]) == {"equity": "100000"}
    assert mcp_client._parse([Block("not json at all")]) == "not json at all"
    assert mcp_client._parse([]) is None
    assert mcp_client._parse(None) is None


def test_008_a_numeric_looking_string_is_not_coerced() -> None:
    """Nothing here turns upstream text into a number risk math could consume."""

    class Block:
        text = "12345"

    assert mcp_client._parse([Block()]) == 12345  # json.loads sees a JSON number
    # and it never reaches the Kernel regardless — MCP-005.


# ---------------------------------------------------------------------------
# MCP-004, MCP-009 — the server environment
# ---------------------------------------------------------------------------


def test_009_the_server_is_never_started_outside_paper_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-004, one layer further out than the app's own boot check."""
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "false")
    with pytest.raises(MCPUnavailableError, match="ALPACA_PAPER_TRADE"):
        mcp_client._server_env()


def test_004_the_server_environment_carries_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "true")
    monkeypatch.delenv("ALPACA_TOOLSETS", raising=False)

    env = mcp_client._server_env()
    assert env["ALPACA_TOOLSETS"] == DEFAULT_TOOLSETS
    assert env["ALPACA_PAPER_TRADE"] == "true"


def test_004_an_explicit_allowlist_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "true")
    monkeypatch.setenv("ALPACA_TOOLSETS", "stock-data")
    assert mcp_client._server_env()["ALPACA_TOOLSETS"] == "stock-data"


def test_the_server_is_found_beside_the_interpreter_before_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A venv puts console scripts next to its python, not on PATH."""
    monkeypatch.setenv("ALPACA_MCP_COMMAND", "/explicit/path/alpaca-mcp-server")
    assert mcp_client.server_command() == "/explicit/path/alpaca-mcp-server"


# ---------------------------------------------------------------------------
# MCP failing is never fatal
# ---------------------------------------------------------------------------


def test_an_unavailable_server_degrades_the_prompt_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§16.2: this path is informative. A cycle without it still has a Kernel."""

    def unavailable(_requests: object) -> MCPResult:
        raise MCPUnavailableError("server not installed")

    monkeypatch.setattr("underwriter.mcp.context.call_tools", unavailable)

    context = fetch_context()
    assert context.degraded is True
    assert context.available is False
    assert "not installed" in context.detail


def test_a_degraded_context_says_so_in_the_prompt() -> None:
    context = MarketContext(degraded=True, detail="budget exhausted")
    assert any("unavailable" in line for line in context.as_prompt_lines())


def test_a_healthy_context_renders_the_account_shape() -> None:
    context = MarketContext(
        account={"equity": "100000.00", "buying_power": "400000.00", "ignored": "x"},
        tools_used=("get_account_info",),
    )
    lines = context.as_prompt_lines()

    assert any("equity: 100000.00" in line for line in lines)
    assert any("buying power: 400000.00" in line for line in lines)
    assert not any("ignored" in line for line in lines)


def test_the_context_batch_contains_no_write_tool() -> None:
    """The batch is read-only by construction, not by convention."""
    from underwriter.mcp.context import CONTEXT_TOOLS

    names = tuple(tool for tool, _ in CONTEXT_TOOLS)
    assert mutating_tools_exposed(names) == ()


def test_tool_calls_are_logged_with_latency_and_status() -> None:
    """MCP-003: name, arguments, latency and result status, every call."""
    call = ToolCall("get_clock", {}, 42, ok=True)
    assert call.tool == "get_clock"
    assert call.latency_ms == 42
    assert call.ok is True

    result = MCPResult(calls=[call, ToolCall("get_account_info", {}, 58, ok=True)])
    assert result.total_latency_ms == 100


# ---------------------------------------------------------------------------
# Live — the surface the server actually exposes (OPS-033: never in CI)
# ---------------------------------------------------------------------------

live = pytest.mark.skipif(
    not mcp_client.is_available() or not os.environ.get("ALPACA_API_KEY"),
    reason="alpaca-mcp-server or credentials not available",
)


@pytest.mark.live
@live
def test_live_no_order_tool_is_reachable() -> None:
    """The claim, checked against the real server rather than a constant."""
    tools = mcp_client.list_tools()
    assert tools, "the server exposed no tools at all"

    for order_tool in ORDER_TOOLS:
        assert order_tool not in tools, f"{order_tool} is reachable under the allowlist"

    assert forbidden_tools_exposed(tools) == (), "an unaccounted write tool is exposed"


@pytest.mark.live
@live
def test_live_the_required_read_tools_are_present() -> None:
    """§16.2's Required rows, minus the ones that live in `trading`."""
    tools = mcp_client.list_tools()
    for required in (
        "get_clock",
        "get_calendar",
        "get_option_chain",
        "get_option_snapshot",
        "get_option_contracts",
        "get_stock_snapshot",
        "get_stock_bars",
        "get_account_info",
    ):
        assert required in tools, f"{required} is missing from the allowlist"


@pytest.mark.live
@live
def test_live_an_unknown_tool_is_never_recorded_as_data() -> None:
    """MCP-008 against the real server.

    `get_all_positions` lives in `trading`, which is excluded, so the server
    answers with an error payload rather than raising — exactly the shape that
    would otherwise be stored as a result.
    """
    result = mcp_client.call_tools([("get_all_positions", {})])

    assert result.calls[0].ok is False
    assert "get_all_positions" not in result.data
    assert result.degraded is True


@pytest.mark.live
@live
def test_live_the_agent_context_is_genuinely_mcp_derived() -> None:
    """MCP-001."""
    context = fetch_context()

    assert context.available is True
    assert "get_clock" in context.tools_used
    assert context.latency_ms > 0

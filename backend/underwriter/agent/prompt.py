"""Prompt assembly — FR-045, and the candidate table the model reasons over.

The system prompt is a version-controlled file, not a string literal, and its
SHA-256 is recorded with every decision. That is what makes a decision
reproducible six weeks later: the same prompt, the same model, the same
snapshot, the same answer.

The user message is built entirely from Actuary output. No free text from any
external source reaches it, which is the cheapest possible defence against
SEC-011 — there is no untrusted string in the prompt to inject through.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from underwriter.domain.proposal import UnderwritingProposal

PROMPT_DIR = Path(__file__).parent / "prompts"
ACTIVE_PROMPT = "system_v1.md"


@dataclass(frozen=True, slots=True)
class SystemPrompt:
    version: str
    text: str
    sha256: str


@lru_cache(maxsize=4)
def load_system_prompt(filename: str = ACTIVE_PROMPT) -> SystemPrompt:
    """Read the versioned prompt and hash it (FR-045)."""
    path = PROMPT_DIR / filename
    text = path.read_text(encoding="utf-8")
    return SystemPrompt(
        version=path.stem,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    """What the book already holds, so the model can reason about fit."""

    nav: Decimal
    open_policies: int
    reserve_utilization_pct: Decimal
    net_delta: Decimal
    net_vega: Decimal
    underlyings_held: tuple[str, ...] = ()
    recent_settlements: tuple[str, ...] = ()
    mode: str = "ACTIVE"
    # MCP-001: assembled through MCP tools, so the model's view of the world
    # is genuinely MCP-derived. MCP-005 keeps it out of the Kernel — this
    # informs judgment; REST bounds authority.
    mcp_lines: tuple[str, ...] = ()


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def candidate_table(proposals: tuple[UnderwritingProposal, ...]) -> str:
    """A compact fixed-width table of pre-priced candidates.

    Every column is an Actuary output. The model is told in the system prompt
    that these are authoritative and that it performs no arithmetic, so the
    table is the whole of its factual world.
    """
    if not proposals:
        return "No candidates qualified this cycle."

    header = (
        f"{'candidate_id':<24} {'sym':<5} {'exp':<11} {'dte':>3} "
        f"{'short':>7} {'long':>7} {'credit':>7} {'maxloss':>9} "
        f"{'edge':>7} {'liq':>6} {'delta':>7}"
    )
    lines = [header, "-" * len(header)]

    for p in proposals:
        lines.append(
            f"{p.candidate_id:<24} {p.underlying:<5} {p.expiry.isoformat():<11} {p.dte:>3} "
            f"{_money(p.short_strike):>7} {_money(p.long_strike):>7} "
            f"{_money(p.net_credit):>7} {_money(p.max_loss):>9} "
            f"{p.edge_ratio:>7} {p.liquidity_score:>6} {p.short_delta:>7}"
        )

    return "\n".join(lines)


def portfolio_block(context: PortfolioContext) -> str:
    held = ", ".join(context.underlyings_held) if context.underlyings_held else "none"
    settlements = (
        "\n".join(f"  - {s}" for s in context.recent_settlements)
        if context.recent_settlements
        else "  (none yet)"
    )
    return (
        f"NAV: ${_money(context.nav)}\n"
        f"Open policies: {context.open_policies}\n"
        f"Reserve utilisation: {context.reserve_utilization_pct}% of the deployable ceiling\n"
        f"Portfolio net delta: {context.net_delta}\n"
        f"Portfolio net vega: {context.net_vega}\n"
        f"Underlyings already held: {held}\n"
        f"System mode: {context.mode}\n"
        f"Recent settlements:\n{settlements}"
    )


def build_user_message(
    proposals: tuple[UnderwritingProposal, ...], context: PortfolioContext
) -> str:
    """The cycle's whole factual input, assembled from Actuary output only."""
    return (
        "# Portfolio context\n\n"
        f"{portfolio_block(context)}\n\n"
        "# Candidates priced by the Actuary this cycle\n\n"
        "All figures are pre-computed and authoritative. Credit is quoted "
        "conservatively (short bid, long ask). `edge` is expected value per unit "
        "of max loss under a full-loss model. `liq` is the worse leg's liquidity "
        "score, 0 to 1. `delta` is the short leg's delta, a risk-neutral "
        "approximation of assignment probability.\n\n"
        f"```\n{candidate_table(proposals)}\n```\n\n"
        "# Your decision\n\n"
        "Select at most one candidate to write, or DECLINE. "
        "If you write, name the specific risks you are accepting.\n"
        f"You may only reference these {len(proposals)} candidate ids."
    )

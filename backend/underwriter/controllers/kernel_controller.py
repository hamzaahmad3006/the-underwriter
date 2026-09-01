"""Kernel endpoints — API-043, API-044, API-045.

API-045 is the demo weapon. It hands a hypothetical proposal to the **real**
Kernel — the same `evaluate()` the scheduler calls, not a mock — and returns
the full verdict with per-rule detail. `executed` is `false` on every response
because this path has no execution engine behind it at all, which is a stronger
guarantee than a flag: there is nothing here to transmit an order with.

TEST-035 points a deliberately catastrophic proposal at it (90% of NAV, naked
short, 0DTE, unknown candidate) and asserts the rejection names the right rules.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from underwriter.controllers.serializers import verdict_to_dict
from underwriter.domain.market import OptionRight, Side
from underwriter.domain.proposal import (
    Action,
    SpreadLeg,
    Structure,
    UnderwritingProposal,
)
from underwriter.kernel import kernel
from underwriter.kernel.context import AccountState, KernelContext, SystemMode
from underwriter.kernel.limits import DEFAULT_LIMITS
from underwriter.kernel.rules import RULES
from underwriter.middleware.error_handler import EndpointNotReadyError

# Generated per process rather than hardcoded. A simulation verdict must never
# verify against the real signing key, and a fixed literal in source is exactly
# the thing someone later mistakes for one.
SIMULATION_SECRET = secrets.token_hex(32)


class SimulateRequest(BaseModel):
    """A proposal-shaped body, including deliberately catastrophic ones."""

    underlying: str = Field(default="SPY", pattern=r"^[A-Z]{1,6}$")
    action: Action = Action.OPEN
    short_strike: Decimal = Decimal("550")
    long_strike: Decimal = Decimal("548")
    dte: int = 17
    requested_contracts: int = Field(default=1, ge=0, le=10_000)

    net_credit: Decimal = Decimal("0.50")
    max_profit: Decimal = Decimal("50.00")
    max_loss: Decimal = Decimal("150.00")
    capital_reserve: Decimal | None = None

    edge_ratio: Decimal = Decimal("0.10")
    liquidity_score: Decimal = Decimal("0.80")
    max_leg_spread_pct: Decimal = Decimal("0.05")
    short_delta: Decimal = Decimal("-0.20")
    net_delta: Decimal = Decimal("5")
    net_vega: Decimal = Decimal("-7")
    greeks_complete: bool = True

    naked: bool = Field(
        default=False,
        description="Drop the long leg, making the risk undefined. Trips SK-004.",
    )
    candidate_id: str = "sim_candidate"
    candidate_is_known: bool = Field(
        default=True,
        description="False simulates a hallucinated instrument. Trips SK-024.",
    )

    # Account context, so a caller can simulate an exhausted or halted book.
    nav: Decimal = Decimal("100000")
    buying_power: Decimal = Decimal("200000")
    equity: Decimal | None = None
    peak_equity: Decimal | None = None
    daily_realized_pnl: Decimal = Decimal("0")
    mode: SystemMode = SystemMode.ACTIVE
    kill_switch_engaged: bool = False
    market_open: bool = True
    data_age_sec: int = Field(default=5, ge=0)


def _build_proposal(body: SimulateRequest, now: datetime) -> UnderwritingProposal:
    expiry = date.fromordinal(now.date().toordinal() + max(body.dte, 0))

    short_leg = SpreadLeg(
        symbol=f"{body.underlying}_SIM_SHORT",
        right=OptionRight.PUT,
        side=Side.SELL,
        strike=body.short_strike,
        expiry=expiry,
    )
    long_leg = SpreadLeg(
        symbol=f"{body.underlying}_SIM_LONG",
        right=OptionRight.PUT,
        side=Side.BUY,
        strike=body.long_strike,
        expiry=expiry,
    )
    legs = (short_leg,) if body.naked else (short_leg, long_leg)

    return UnderwritingProposal(
        candidate_id=body.candidate_id,
        underlying=body.underlying,
        structure=Structure.PUT_CREDIT_SPREAD,
        action=body.action,
        legs=legs,
        short_strike=body.short_strike,
        long_strike=body.long_strike,
        expiry=expiry,
        dte=body.dte,
        net_credit=body.net_credit,
        max_profit=body.max_profit,
        max_loss=body.max_loss,
        capital_reserve=(body.max_loss if body.capital_reserve is None else body.capital_reserve),
        breakeven=body.short_strike - body.net_credit,
        credit_to_width=Decimal("0.25"),
        p_loss_proxy=abs(body.short_delta),
        p_profit_proxy=Decimal("1") - abs(body.short_delta),
        expected_value=Decimal("0"),
        edge_ratio=body.edge_ratio,
        liquidity_score=body.liquidity_score,
        max_leg_spread_pct=body.max_leg_spread_pct,
        short_delta=body.short_delta,
        net_delta=body.net_delta,
        net_vega=body.net_vega,
        greeks_complete=body.greeks_complete,
        snapshot_hash="simulation",
        snapshot_as_of=now,
    )


def _build_context(body: SimulateRequest, now: datetime, candidate_id: str) -> KernelContext:
    account = AccountState(
        nav=body.nav,
        equity=body.nav if body.equity is None else body.equity,
        buying_power=body.buying_power,
        peak_equity=body.nav if body.peak_equity is None else body.peak_equity,
        daily_realized_pnl=body.daily_realized_pnl,
        as_of=now,
    )
    return KernelContext(
        now=now,
        account=account,
        data_as_of=datetime.fromtimestamp(now.timestamp() - body.data_age_sec, tz=UTC),
        mode=body.mode,
        kill_switch_engaged=body.kill_switch_engaged,
        market_open=body.market_open,
        supplied_candidate_ids=(
            frozenset({candidate_id}) if body.candidate_is_known else frozenset()
        ),
    )


def simulate(body: SimulateRequest) -> dict[str, object]:
    """API-045. Adjudicate a hypothetical proposal without executing anything."""
    now = datetime.now(UTC)
    proposal = _build_proposal(body, now)
    context = _build_context(body, now, proposal.candidate_id)

    verdict = kernel.evaluate(
        proposal,
        requested_contracts=body.requested_contracts,
        context=context,
        secret=os.environ.get("KERNEL_SIGNING_SECRET") or SIMULATION_SECRET,
    )

    return {
        "as_of": now.isoformat(),
        "executed": False,
        "explanation": kernel.explain(verdict),
        "verdict": verdict_to_dict(verdict),
    }


def list_decisions(verdict: str | None, limit: int) -> dict[str, object]:
    """API-043 — the Kernel Veto Feed, §21.5's signature panel."""
    raise EndpointNotReadyError(
        "The Kernel Veto Feed",
        "kernel_decisions is not persisted yet (DB-007). Use POST /kernel/simulate, "
        "which exercises the same rule table live.",
    )


def get_decision(decision_id: str) -> dict[str, object]:
    """API-044 — the full rule breakdown for one stored verdict."""
    raise EndpointNotReadyError(
        "Stored kernel verdicts",
        "kernel_decisions is not persisted yet (DB-007).",
    )


def rule_table() -> dict[str, object]:
    """API-041's rule half: the active limits, straight off the Kernel.

    Judges prefer reading rules as code (TD-10), so this returns the real
    configured limits rather than a copy maintained by hand.
    """
    limits = DEFAULT_LIMITS
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "limits": {
            "SK-001_max_deployed_pct": str(limits.max_deployed_pct),
            "SK-003_max_position_loss_pct": str(limits.max_position_loss_pct),
            "SK-005_max_open_policies": limits.max_open_policies,
            "SK-006_max_underlying_concentration": str(limits.max_underlying_concentration),
            "SK-007_max_portfolio_risk_pct": str(limits.max_portfolio_risk_pct),
            "SK-008_max_net_delta_per_10k": str(limits.max_net_delta_per_10k),
            "SK-009_max_vega_per_100k": str(limits.max_vega_per_100k),
            "SK-010_max_daily_loss_pct": str(limits.max_daily_loss_pct),
            "SK-011_min_dte_at_entry": limits.min_dte_at_entry,
            "SK-014_min_liquidity_score": str(limits.min_liquidity_score),
            "SK-015_max_bid_ask_pct": str(limits.max_bid_ask_pct),
            "SK-016_min_edge_ratio": str(limits.min_edge_ratio),
            "SK-018_max_data_age_sec": limits.max_data_age_sec,
            "SK-022_max_drawdown_pct": str(limits.max_drawdown_pct),
            "verdict_ttl_sec": limits.verdict_ttl_sec,
        },
        "rules": [
            {"rule_id": spec.rule_id, "name": spec.name, "severity": spec.severity}
            for spec in RULES
        ],
    }

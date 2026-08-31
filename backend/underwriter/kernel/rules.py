"""The rule table — SRS §14.3, one function per rule.

SK-P7 is a real constraint on this file: the table has to be legible to a
non-author in one sitting, because a rule nobody understands is a rule nobody
trusts. So every rule is a small named function with the same shape, and the
registry at the bottom is the whole table in one screen.

Every rule is evaluated for **one contract** of headroom. Whether more than one
contract fits is a sizing question (§15.5), answered in `kernel.py`. A rule
fails only when not even a single spread can be written.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from underwriter.domain.market import OptionRight, Side
from underwriter.domain.money import CONTRACT_MULTIPLIER, ZERO
from underwriter.domain.proposal import Action, UnderwritingProposal
from underwriter.kernel.context import KernelContext, SystemMode
from underwriter.kernel.limits import KernelLimits
from underwriter.kernel.verdict import RuleResult, Severity

TEN_K = Decimal("10000")
HUNDRED_K = Decimal("100000")


def client_order_id_for(proposal_hash: str) -> str:
    """Deterministic from the proposal (TEST-051).

    Derived rather than random so that a retry after an ambiguous network
    failure collides with the original instead of double-submitting (F-15).
    """
    return f"uw_{proposal_hash[:24]}"


def money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _result(
    rule_id: str,
    name: str,
    passed: bool,
    severity: Severity,
    observed: str,
    limit: str,
    message: str,
    reason_code: str,
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        name=name,
        passed=passed,
        severity=severity,
        observed=observed,
        limit=limit,
        message=message,
        reason_code="" if passed else reason_code,
    )


def _exempt(rule_id: str, name: str) -> RuleResult:
    """SK-000 in action: a closing order skips the capital rules."""
    return _result(
        rule_id,
        name,
        True,
        Severity.HARD,
        "risk-reducing close",
        "capital rules skipped",
        "Exempt under SK-000: this action strictly reduces portfolio risk.",
        "",
    )


# ---------------------------------------------------------------------------
# SK-000 — the exemption itself, recorded so the ledger shows it applied
# ---------------------------------------------------------------------------


def sk000_risk_reducing(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    closing = proposal.action is Action.CLOSE
    return _result(
        "SK-000",
        "risk_reducing_exemption",
        True,
        Severity.HARD,
        f"action={proposal.action}",
        "capital rules skipped for CLOSE",
        (
            "Closing order: SK-001/002/003/007 skipped. All safety rules still apply."
            if closing
            else "Opening order: all rules apply."
        ),
        "",
    )


# ---------------------------------------------------------------------------
# Capital — skipped for risk-reducing closes (SK-000)
# ---------------------------------------------------------------------------


def sk001_max_deployed(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    if proposal.action is Action.CLOSE:
        return _exempt("SK-001", "max_capital_deployed")
    ceiling = context.account.nav * limits.max_deployed_pct
    after = context.total_reserve + proposal.capital_reserve
    return _result(
        "SK-001",
        "max_capital_deployed",
        after <= ceiling,
        Severity.HARD,
        f"deployed_after={money(after)}",
        f"max_deployed={money(ceiling)} ({limits.max_deployed_pct:%} of NAV)",
        "Total reserved capital across all open policies.",
        "MAX_DEPLOYED",
    )


def sk002_full_reserve(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    if proposal.action is Action.CLOSE:
        return _exempt("SK-002", "fully_reserved")
    exact = proposal.capital_reserve == proposal.max_loss
    return _result(
        "SK-002",
        "fully_reserved",
        exact,
        Severity.HARD,
        f"reserve={money(proposal.capital_reserve)}",
        f"max_loss={money(proposal.max_loss)}",
        "Every policy is reserved at exactly its maximum loss. No partial reserving.",
        "UNDER_RESERVED",
    )


def sk003_position_loss_limit(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    if proposal.action is Action.CLOSE:
        return _exempt("SK-003", "position_loss_limit")
    ceiling = context.account.nav * limits.max_position_loss_pct
    return _result(
        "SK-003",
        "position_loss_limit",
        proposal.max_loss <= ceiling,
        Severity.HARD,
        f"max_loss={money(proposal.max_loss)}",
        f"limit={money(ceiling)} ({limits.max_position_loss_pct:%} of NAV)",
        "Maximum loss on any single policy.",
        "POSITION_LOSS_LIMIT",
    )


def sk007_max_portfolio_risk(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    if proposal.action is Action.CLOSE:
        return _exempt("SK-007", "max_portfolio_risk")
    ceiling = context.account.nav * limits.max_portfolio_risk_pct
    after = context.portfolio_max_loss + proposal.max_loss
    return _result(
        "SK-007",
        "max_portfolio_risk",
        after <= ceiling,
        Severity.HARD,
        f"portfolio_max_loss_after={money(after)}",
        f"limit={money(ceiling)} ({limits.max_portfolio_risk_pct:%} of NAV)",
        "Aggregate loss if every open policy lost its maximum simultaneously.",
        "MAX_PORTFOLIO_RISK",
    )


# ---------------------------------------------------------------------------
# Structure and book shape
# ---------------------------------------------------------------------------


def sk004_defined_risk(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """Every short leg covered by a long leg of the same type and underlying,
    at a longer-or-equal expiry, with a finite computable max loss.

    This is the rule that makes the whole product possible: an undefined-risk
    position has no max loss to reserve against, so nothing downstream can be
    bounded.
    """
    shorts = [leg for leg in proposal.legs if leg.side is Side.SELL]
    longs = [leg for leg in proposal.legs if leg.side is Side.BUY]

    covered = True
    detail = "every short leg covered"
    for short in shorts:
        cover = [
            long
            for long in longs
            if long.right is short.right
            and long.expiry >= short.expiry
            and (short.right is not OptionRight.PUT or long.strike < short.strike)
        ]
        if not cover:
            covered = False
            detail = f"{short.symbol} has no covering long leg"
            break

    finite_loss = proposal.max_loss.is_finite() and proposal.max_loss > ZERO
    if covered and not finite_loss:
        detail = f"max_loss={proposal.max_loss} is not a positive finite number"

    return _result(
        "SK-004",
        "defined_risk_only",
        covered and finite_loss and len(shorts) > 0,
        Severity.HARD,
        detail,
        "every short leg covered, max_loss finite and positive",
        "Undefined risk cannot be reserved against, so it cannot be written.",
        "UNDEFINED_RISK",
    )


def sk005_max_positions(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    # A close adds no policy, so the count cannot be the thing that blocks it.
    after = len(context.open_policies) + (0 if proposal.action is Action.CLOSE else 1)
    return _result(
        "SK-005",
        "max_open_policies",
        after <= limits.max_open_policies,
        Severity.HARD,
        f"open_after={after}",
        f"max={limits.max_open_policies}",
        "Simultaneous open policies the Claims Desk can actually manage.",
        "MAX_POSITIONS",
    )


def sk006_concentration(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """Per-underlying exposure cap.

    §14.3 words the base as "total deployed reserve", but §15.5 shows that
    measured against *currently* deployed reserve the first trade can never
    pass: with nothing deployed the base is zero, so any exposure is infinite
    concentration. Measured instead against the deployable ceiling
    (NAV x SK-001), the rule means what it is clearly meant to mean — no single
    underlying may hold more than 25% of the capital the firm is willing to put
    at risk, here 15% of NAV. Recorded as a deviation in the SRS.
    """
    if proposal.action is Action.CLOSE:
        return _exempt("SK-006", "underlying_concentration")

    base = context.account.nav * limits.max_deployed_pct
    ceiling = base * limits.max_underlying_concentration
    after = context.reserve_for(proposal.underlying) + proposal.capital_reserve
    return _result(
        "SK-006",
        "underlying_concentration",
        after <= ceiling,
        Severity.HARD,
        f"{proposal.underlying}_reserve_after={money(after)}",
        f"limit={money(ceiling)} ({limits.max_underlying_concentration:%} of deployable)",
        "Exposure concentrated in one underlying.",
        "CONCENTRATION",
    )


def sk012_assignment_cost(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """If every short leg were assigned at once, could we take delivery?

    The long leg bounds the loss but not the transient cash requirement — you
    take delivery at the short strike first and exercise the long second.
    """
    if proposal.action is Action.CLOSE:
        return _exempt("SK-012", "assignment_cost")

    this_cost = proposal.short_strike * CONTRACT_MULTIPLIER
    total = context.total_assignment_cost + this_cost
    return _result(
        "SK-012",
        "assignment_cost",
        total <= context.account.buying_power,
        Severity.HARD,
        f"assignment_cost={money(total)}",
        f"buying_power={money(context.account.buying_power)}",
        "Cash required if every short leg were assigned simultaneously.",
        "ASSIGNMENT_COST",
    )


def sk013_breach_active(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """Underlying already through a short strike: no new policies on it."""
    if proposal.action is Action.CLOSE:
        return _result(
            "SK-013",
            "short_strike_breach",
            True,
            Severity.HARD,
            "action=CLOSE",
            "closes always permitted",
            "A breach is a reason to close, never a reason to block closing.",
            "",
        )
    breached = proposal.underlying in context.breached_underlyings
    return _result(
        "SK-013",
        "short_strike_breach",
        not breached,
        Severity.HARD,
        f"{proposal.underlying} breached={breached}",
        "no active breach on this underlying",
        "Underlying has traded through a short strike; the book is already exposed there.",
        "BREACH_ACTIVE",
    )


def sk021_duplicate(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """No identical open policy, and no client_order_id collision (F-12)."""
    identical = any(
        p.underlying == proposal.underlying
        and p.structure is proposal.structure
        and p.short_strike == proposal.short_strike
        and p.long_strike == proposal.long_strike
        and p.expiry == proposal.expiry
        for p in context.open_policies
    )
    coid = client_order_id_for(proposal.proposal_hash)
    collision = coid in context.existing_client_order_ids
    return _result(
        "SK-021",
        "duplicate_prevention",
        not (identical or collision),
        Severity.HARD,
        f"identical_policy={identical} coid_collision={collision}",
        "no identical open policy, no client_order_id collision",
        "The same policy written twice is one position with two reserves.",
        "DUPLICATE",
    )


# ---------------------------------------------------------------------------
# Greeks — SOFT: these reduce size, they never reject (§14.2)
# ---------------------------------------------------------------------------


def sk008_delta_limit(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    nav_units = context.account.nav / TEN_K if context.account.nav > ZERO else ZERO
    ceiling = limits.max_net_delta_per_10k * nav_units
    after = abs(context.portfolio_net_delta + proposal.net_delta)
    return _result(
        "SK-008",
        "portfolio_delta_limit",
        after <= ceiling,
        Severity.SOFT,
        f"net_delta_after={after}",
        f"limit={ceiling} ({limits.max_net_delta_per_10k} per $10k NAV)",
        "Directional exposure across the book. Soft: reduces size, never rejects.",
        "DELTA_LIMIT",
    )


def sk009_vega_limit(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    nav_units = context.account.nav / HUNDRED_K if context.account.nav > ZERO else ZERO
    ceiling = limits.max_vega_per_100k * nav_units
    after = abs(context.portfolio_net_vega + proposal.net_vega)
    return _result(
        "SK-009",
        "portfolio_vega_limit",
        after <= ceiling,
        Severity.SOFT,
        f"net_vega_after={after}",
        f"limit={ceiling} ({limits.max_vega_per_100k} per $100k NAV)",
        "Volatility exposure across the book. Soft: reduces size, never rejects.",
        "VEGA_LIMIT",
    )


# ---------------------------------------------------------------------------
# Loss control and mode
# ---------------------------------------------------------------------------


def sk010_daily_loss_halt(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    floor = -(context.account.nav * limits.max_daily_loss_pct)
    return _result(
        "SK-010",
        "daily_loss_halt",
        context.account.daily_realized_pnl >= floor,
        Severity.HARD,
        f"daily_realized={money(context.account.daily_realized_pnl)}",
        f"floor={money(floor)} ({limits.max_daily_loss_pct:%} of NAV)",
        "Breach forces MANAGE_ONLY for the rest of the session.",
        "DAILY_LOSS_HALT",
    )


def sk022_drawdown_halt(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    dd = context.account.drawdown_pct
    return _result(
        "SK-022",
        "drawdown_halt",
        dd <= limits.max_drawdown_pct,
        Severity.HARD,
        f"drawdown={dd:.2%}",
        f"limit={limits.max_drawdown_pct:%} from peak equity",
        "Breach forces MANAGE_ONLY for the event; re-arming is manual and logged.",
        "DRAWDOWN_HALT",
    )


def sk023_mode_gate(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """Kill switch and mode.

    HALT blocks everything including closes — it is the state the system enters
    when it no longer trusts its own view of the book, and guessing there would
    be worse than doing nothing. MANAGE_ONLY blocks entries but must permit
    closes, since managing down the book is the entire point of that mode.
    """
    if context.kill_switch_engaged or context.mode is SystemMode.HALT:
        return _result(
            "SK-023",
            "mode_gate",
            False,
            Severity.HARD,
            f"mode={context.mode} kill_switch={context.kill_switch_engaged}",
            "ACTIVE required",
            "Kill switch engaged or system halted.",
            "KILL_SWITCH" if context.kill_switch_engaged else "MODE_BLOCKED",
        )

    permitted = context.mode is SystemMode.ACTIVE or proposal.action is Action.CLOSE
    return _result(
        "SK-023",
        "mode_gate",
        permitted,
        Severity.HARD,
        f"mode={context.mode} action={proposal.action}",
        "ACTIVE for entries; MANAGE_ONLY permits closes only",
        "Entries require ACTIVE mode.",
        "MODE_BLOCKED",
    )


# ---------------------------------------------------------------------------
# Data quality, timing and provenance
# ---------------------------------------------------------------------------


def sk011_min_dte(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """Min DTE at entry.

    Alpaca publishes no Greeks at 0DTE — time to expiry sits in the
    Black-Scholes denominator, so the value is undefined at expiry. A position
    held into 0DTE becomes unmeasurable by this system's own risk model, so the
    entry floor and FORCE_FLAT_DTE together guarantee the book never holds risk
    it cannot measure (G-08).
    """
    if proposal.action is Action.CLOSE:
        return _result(
            "SK-011",
            "min_dte_at_entry",
            True,
            Severity.HARD,
            "action=CLOSE",
            "entry floor does not apply to exits",
            "Closing a short-dated position is exactly what FORCE_FLAT_DTE requires.",
            "",
        )
    return _result(
        "SK-011",
        "min_dte_at_entry",
        proposal.dte >= limits.min_dte_at_entry,
        Severity.HARD,
        f"dte={proposal.dte}",
        f"min={limits.min_dte_at_entry} calendar days",
        "No entry that could be held into an unmeasurable 0DTE position.",
        "DTE_TOO_SHORT",
    )


def sk014_liquidity(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    return _result(
        "SK-014",
        "min_liquidity",
        proposal.liquidity_score >= limits.min_liquidity_score,
        Severity.HARD,
        f"liquidity_score={proposal.liquidity_score}",
        f"min={limits.min_liquidity_score}",
        "An illiquid spread cannot be exited at a fair price when it matters.",
        "ILLIQUID",
    )


def sk015_leg_spread(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    return _result(
        "SK-015",
        "max_leg_spread",
        proposal.max_leg_spread_pct <= limits.max_bid_ask_pct,
        Severity.HARD,
        f"worst_leg_spread={proposal.max_leg_spread_pct}",
        f"max={limits.max_bid_ask_pct} of mid",
        "A wide quote means the modelled credit is not the credit we would get.",
        "WIDE_SPREAD",
    )


def sk016_edge(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    return _result(
        "SK-016",
        "min_edge_ratio",
        proposal.edge_ratio >= limits.min_edge_ratio,
        Severity.HARD,
        f"edge_ratio={proposal.edge_ratio}",
        f"min={limits.min_edge_ratio}",
        "Expected value per unit risked, under the conservative binary model.",
        "INSUFFICIENT_EDGE",
    )


def sk017_market_hours(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """Market open, and outside the open/close blackout windows.

    The blackout applies to entries only. It exists because pricing is unstable
    at the bells; refusing to *close* in that window would strand risk the
    Claims Desk is obliged to remove.
    """
    if not context.market_open:
        return _result(
            "SK-017",
            "market_hours",
            False,
            Severity.HARD,
            "market_open=False",
            "regular session required",
            "The market is closed.",
            "MARKET_CLOSED",
        )

    if proposal.action is Action.CLOSE:
        return _result(
            "SK-017",
            "market_hours",
            True,
            Severity.HARD,
            "action=CLOSE, market open",
            "blackout applies to entries only",
            "Exits are permitted throughout the session.",
            "",
        )

    inside_blackout = (
        context.minutes_since_open < limits.blackout_open_min
        or context.minutes_to_close < limits.blackout_close_min
    )
    return _result(
        "SK-017",
        "market_hours",
        not inside_blackout,
        Severity.HARD,
        f"since_open={context.minutes_since_open}m to_close={context.minutes_to_close}m",
        f"blackout +{limits.blackout_open_min}m / -{limits.blackout_close_min}m",
        "No entries inside the opening or closing blackout windows.",
        "MARKET_CLOSED",
    )


def sk018_data_freshness(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    age = context.data_age_sec
    return _result(
        "SK-018",
        "data_freshness",
        age <= limits.max_data_age_sec,
        Severity.HARD,
        f"data_age={age}s",
        f"max={limits.max_data_age_sec}s",
        "Every input must be younger than MAX_DATA_AGE_SEC at decision time.",
        "STALE_DATA",
    )


def sk019_greeks_complete(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    return _result(
        "SK-019",
        "greeks_complete",
        proposal.greeks_complete,
        Severity.HARD,
        f"greeks_complete={proposal.greeks_complete}",
        "delta and vega present on every leg",
        "Missing Greeks are never estimated; the candidate is discarded instead.",
        "MISSING_GREEKS",
    )


def sk024_llm_output_integrity(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    """The candidate must be one we supplied.

    This is the rule that catches a hallucinated or injected instrument: the
    LLM can only choose from the set the Actuary priced, and anything else is
    rejected and raised as a possible injection (F-09).
    """
    known = proposal.candidate_id in context.supplied_candidate_ids
    return _result(
        "SK-024",
        "llm_output_integrity",
        known,
        Severity.HARD,
        f"candidate_id={proposal.candidate_id}",
        f"must be one of {len(context.supplied_candidate_ids)} supplied candidates",
        "The proposal references an instrument that was never offered to the model.",
        "LLM_OUTPUT_INVALID",
    )


def sk025_account_readable(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> RuleResult:
    return _result(
        "SK-025",
        "account_readable",
        context.account.read_ok,
        Severity.HARD,
        f"read_ok={context.account.read_ok}",
        "authoritative account read at decision time",
        "SK-P5: account state comes from Alpaca REST now, never from cache.",
        "ACCOUNT_UNAVAILABLE",
    )


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

Evaluator = Callable[[UnderwritingProposal, KernelContext, KernelLimits], RuleResult]


@dataclass(frozen=True, slots=True)
class RuleSpec:
    rule_id: str
    name: str
    severity: Severity
    evaluate: Evaluator


RULES: tuple[RuleSpec, ...] = (
    RuleSpec("SK-000", "risk_reducing_exemption", Severity.HARD, sk000_risk_reducing),
    RuleSpec("SK-001", "max_capital_deployed", Severity.HARD, sk001_max_deployed),
    RuleSpec("SK-002", "fully_reserved", Severity.HARD, sk002_full_reserve),
    RuleSpec("SK-003", "position_loss_limit", Severity.HARD, sk003_position_loss_limit),
    RuleSpec("SK-004", "defined_risk_only", Severity.HARD, sk004_defined_risk),
    RuleSpec("SK-005", "max_open_policies", Severity.HARD, sk005_max_positions),
    RuleSpec("SK-006", "underlying_concentration", Severity.HARD, sk006_concentration),
    RuleSpec("SK-007", "max_portfolio_risk", Severity.HARD, sk007_max_portfolio_risk),
    RuleSpec("SK-008", "portfolio_delta_limit", Severity.SOFT, sk008_delta_limit),
    RuleSpec("SK-009", "portfolio_vega_limit", Severity.SOFT, sk009_vega_limit),
    RuleSpec("SK-010", "daily_loss_halt", Severity.HARD, sk010_daily_loss_halt),
    RuleSpec("SK-011", "min_dte_at_entry", Severity.HARD, sk011_min_dte),
    RuleSpec("SK-012", "assignment_cost", Severity.HARD, sk012_assignment_cost),
    RuleSpec("SK-013", "short_strike_breach", Severity.HARD, sk013_breach_active),
    RuleSpec("SK-014", "min_liquidity", Severity.HARD, sk014_liquidity),
    RuleSpec("SK-015", "max_leg_spread", Severity.HARD, sk015_leg_spread),
    RuleSpec("SK-016", "min_edge_ratio", Severity.HARD, sk016_edge),
    RuleSpec("SK-017", "market_hours", Severity.HARD, sk017_market_hours),
    RuleSpec("SK-018", "data_freshness", Severity.HARD, sk018_data_freshness),
    RuleSpec("SK-019", "greeks_complete", Severity.HARD, sk019_greeks_complete),
    RuleSpec("SK-021", "duplicate_prevention", Severity.HARD, sk021_duplicate),
    RuleSpec("SK-022", "drawdown_halt", Severity.HARD, sk022_drawdown_halt),
    RuleSpec("SK-023", "mode_gate", Severity.HARD, sk023_mode_gate),
    RuleSpec("SK-024", "llm_output_integrity", Severity.HARD, sk024_llm_output_integrity),
    RuleSpec("SK-025", "account_readable", Severity.HARD, sk025_account_readable),
)
# SK-020 (size floor) is not in this table: it can only be evaluated after
# sizing, so `kernel.py` appends it once the permitted contract count is known.

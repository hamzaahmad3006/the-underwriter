"""Rule tests — TEST-040 … TEST-044.

TEST-040 asks for a boundary triple on every rule: below the limit passes, at
the limit passes, above it rejects. "At the limit passes" is the half that
matters — an off-by-one in a risk limit is a silent, permanent mispricing of
how much the firm is willing to lose.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import (
    EXPIRY,
    NOW,
    SECRET,
    make_account,
    make_context,
    make_policy,
    make_proposal,
)
from underwriter.domain.market import OptionRight, Side
from underwriter.domain.proposal import Action, SpreadLeg
from underwriter.kernel import kernel
from underwriter.kernel.context import SystemMode
from underwriter.kernel.rules import RULES, RuleSpec, client_order_id_for
from underwriter.kernel.verdict import FAIL_CLOSED, Decision, RuleResult, Severity


def result_for(rule_id: str, proposal: object, context: object) -> RuleResult:
    verdict = kernel.evaluate(
        proposal,  # type: ignore[arg-type]
        requested_contracts=1,
        context=context,  # type: ignore[arg-type]
        secret=SECRET,
    )
    return next(r for r in verdict.rules if r.rule_id == rule_id)


# ---------------------------------------------------------------------------
# TEST-040 — boundary triples, capital rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reserve", "passes"),
    [("59000.00", True), ("59850.00", True), ("59900.00", False)],
)
def test_040_sk001_max_deployed(reserve: str, passes: bool) -> None:
    """60% of $100k NAV = $60,000 deployed, against a $150 new reserve."""
    context = make_context(open_policies=(make_policy(max_loss=reserve),))
    assert result_for("SK-001", make_proposal(), context).passed is passes


@pytest.mark.parametrize(
    ("reserve", "passes"),
    [("150.00", True), ("149.99", False), ("150.01", False)],
)
def test_040_sk002_reserve_must_equal_max_loss(reserve: str, passes: bool) -> None:
    proposal = make_proposal(max_loss="150.00", capital_reserve=reserve)
    assert result_for("SK-002", proposal, make_context()).passed is passes


@pytest.mark.parametrize(
    ("max_loss", "passes"),
    [("2999.00", True), ("3000.00", True), ("3000.01", False)],
)
def test_040_sk003_position_loss_limit(max_loss: str, passes: bool) -> None:
    """3% of $100k NAV = $3,000 per policy."""
    proposal = make_proposal(max_loss=max_loss)
    assert result_for("SK-003", proposal, make_context()).passed is passes


def test_040_sk004_defined_risk_requires_a_covering_long_leg() -> None:
    short_only = make_proposal(legs=(make_proposal().legs[0],))
    assert result_for("SK-004", short_only, make_context()).passed is False

    covered = make_proposal()
    assert result_for("SK-004", covered, make_context()).passed is True


def test_040_sk004_rejects_a_long_leg_expiring_first() -> None:
    """A long leg that expires before the short leg stops covering it."""
    base = make_proposal()
    early_long = replace(base.legs[1], expiry=EXPIRY - timedelta(days=7))
    proposal = replace(base, legs=(base.legs[0], early_long))
    assert result_for("SK-004", proposal, make_context()).passed is False


def test_040_sk004_rejects_a_non_positive_max_loss() -> None:
    proposal = make_proposal(max_loss="0.00", capital_reserve="0.00")
    assert result_for("SK-004", proposal, make_context()).passed is False


@pytest.mark.parametrize(("count", "passes"), [(6, True), (7, True), (8, False)])
def test_040_sk005_max_open_policies(count: int, passes: bool) -> None:
    """Eight is the ceiling *including* the new policy."""
    policies = tuple(make_policy(policy_id=f"pol_{i}") for i in range(count))
    assert result_for("SK-005", make_proposal(), make_context(open_policies=policies)).passed is (
        passes
    )


@pytest.mark.parametrize(
    ("spy_reserve", "passes"),
    [("14000.00", True), ("14850.00", True), ("14900.00", False)],
)
def test_040_sk006_concentration(spy_reserve: str, passes: bool) -> None:
    """25% of the $60k deployable ceiling = $15,000 in any one underlying."""
    context = make_context(open_policies=(make_policy(underlying="SPY", max_loss=spy_reserve),))
    assert result_for("SK-006", make_proposal(underlying="SPY"), context).passed is passes


def test_040_sk006_first_position_in_an_underlying_is_permitted() -> None:
    """The literal SRS base (currently deployed reserve) makes this impossible.

    With nothing deployed the base would be zero and every first trade would be
    infinite concentration. Measured against the deployable ceiling it passes,
    which is plainly what the rule is for.
    """
    assert result_for("SK-006", make_proposal(), make_context(open_policies=())).passed is True


@pytest.mark.parametrize(
    ("open_loss", "passes"),
    [("14000.00", True), ("14850.00", True), ("14900.00", False)],
)
def test_040_sk007_portfolio_risk(open_loss: str, passes: bool) -> None:
    """15% of $100k NAV = $15,000 if every policy lost its maximum at once."""
    context = make_context(open_policies=(make_policy(max_loss=open_loss),))
    assert result_for("SK-007", make_proposal(), context).passed is passes


# ---------------------------------------------------------------------------
# TEST-040 — soft greek rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("portfolio_delta", "passes"),
    [("100", True), ("145", True), ("146", False)],
)
def test_040_sk008_delta_is_soft(portfolio_delta: str, passes: bool) -> None:
    """15 per $10k NAV = 150 at $100k, against a proposal carrying +5."""
    context = make_context(portfolio_net_delta=portfolio_delta)
    outcome = result_for("SK-008", make_proposal(), context)
    assert outcome.passed is passes
    assert outcome.severity is Severity.SOFT


@pytest.mark.parametrize(
    ("portfolio_vega", "passes"),
    [("50", True), ("57", True), ("58", False)],
)
def test_040_sk009_vega_is_soft(portfolio_vega: str, passes: bool) -> None:
    """50 per $100k NAV = 50 at $100k, against a proposal carrying -7."""
    context = make_context(portfolio_net_vega=portfolio_vega)
    outcome = result_for("SK-009", make_proposal(), context)
    assert outcome.passed is passes
    assert outcome.severity is Severity.SOFT


# ---------------------------------------------------------------------------
# TEST-040 — loss control, mode, data quality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pnl", "passes"),
    [("-2000.00", True), ("-3000.00", True), ("-3000.01", False)],
)
def test_040_sk010_daily_loss_halt(pnl: str, passes: bool) -> None:
    context = make_context(account=make_account(daily_realized_pnl=pnl))
    assert result_for("SK-010", make_proposal(), context).passed is passes


@pytest.mark.parametrize(("dte", "passes"), [(8, True), (7, True), (6, False)])
def test_040_sk011_min_dte(dte: int, passes: bool) -> None:
    assert result_for("SK-011", make_proposal(dte=dte), make_context()).passed is passes


@pytest.mark.parametrize(
    ("buying_power", "passes"),
    [("60000.00", True), ("55000.00", True), ("54999.99", False)],
)
def test_040_sk012_assignment_cost(buying_power: str, passes: bool) -> None:
    """One 550-strike short put needs $55,000 to take delivery."""
    context = make_context(account=make_account(buying_power=buying_power))
    assert result_for("SK-012", make_proposal(), context).passed is passes


def test_040_sk013_breached_underlying_blocks_new_policies() -> None:
    context = make_context(breached_underlyings=frozenset({"SPY"}))
    assert result_for("SK-013", make_proposal(underlying="SPY"), context).passed is False
    assert result_for("SK-013", make_proposal(underlying="QQQ"), context).passed is True


@pytest.mark.parametrize(
    ("score", "passes"),
    [("0.70", True), ("0.55", True), ("0.54", False)],
)
def test_040_sk014_liquidity(score: str, passes: bool) -> None:
    assert result_for("SK-014", make_proposal(liquidity_score=score), make_context()).passed is (
        passes
    )


@pytest.mark.parametrize(
    ("spread", "passes"),
    [("0.10", True), ("0.15", True), ("0.16", False)],
)
def test_040_sk015_leg_spread(spread: str, passes: bool) -> None:
    proposal = make_proposal(max_leg_spread_pct=spread)
    assert result_for("SK-015", proposal, make_context()).passed is passes


@pytest.mark.parametrize(
    ("edge", "passes"),
    [("0.10", True), ("0.05", True), ("0.049", False)],
)
def test_040_sk016_edge_ratio(edge: str, passes: bool) -> None:
    assert result_for("SK-016", make_proposal(edge_ratio=edge), make_context()).passed is passes


@pytest.mark.parametrize(
    ("since_open", "to_close", "market_open", "passes"),
    [
        (60, 60, True, True),
        (15, 30, True, True),  # exactly on both blackout boundaries
        (14, 60, True, False),
        (60, 29, True, False),
        (60, 60, False, False),
    ],
)
def test_040_sk017_market_hours(
    since_open: int, to_close: int, market_open: bool, passes: bool
) -> None:
    context = make_context(
        market_open=market_open, minutes_since_open=since_open, minutes_to_close=to_close
    )
    assert result_for("SK-017", make_proposal(), context).passed is passes


@pytest.mark.parametrize(("age", "passes"), [(60, True), (120, True), (121, False)])
def test_040_sk018_data_freshness(age: int, passes: bool) -> None:
    assert result_for("SK-018", make_proposal(), make_context(data_age_sec=age)).passed is passes


def test_040_sk019_greeks_completeness() -> None:
    assert result_for("SK-019", make_proposal(greeks_complete=False), make_context()).passed is (
        False
    )


def test_040_sk021_duplicate_policy_and_order_id() -> None:
    identical = make_policy(underlying="SPY", short_strike=550, long_strike=548, expiry=EXPIRY)
    context = make_context(open_policies=(identical,))
    assert result_for("SK-021", make_proposal(), context).passed is False

    proposal = make_proposal()
    collision = make_context(
        existing_client_order_ids=frozenset({client_order_id_for(proposal.proposal_hash)})
    )
    assert result_for("SK-021", proposal, collision).passed is False

    assert result_for("SK-021", proposal, make_context()).passed is True


@pytest.mark.parametrize(
    ("equity", "passes"),
    [("95000.00", True), ("90000.00", True), ("89999.00", False)],
)
def test_040_sk022_drawdown(equity: str, passes: bool) -> None:
    """10% below a $100k peak."""
    context = make_context(account=make_account(equity=equity, peak_equity="100000.00"))
    assert result_for("SK-022", make_proposal(), context).passed is passes


def test_040_sk022_drawdown_is_zero_when_peak_is_unusable() -> None:
    context = make_context(account=make_account(peak_equity="0.00"))
    assert result_for("SK-022", make_proposal(), context).passed is True


@pytest.mark.parametrize(
    ("mode", "kill", "action", "passes"),
    [
        (SystemMode.ACTIVE, False, Action.OPEN, True),
        (SystemMode.ACTIVE, True, Action.OPEN, False),
        (SystemMode.MANAGE_ONLY, False, Action.OPEN, False),
        (SystemMode.MANAGE_ONLY, False, Action.CLOSE, True),
        (SystemMode.HALT, False, Action.CLOSE, False),
    ],
)
def test_040_sk023_mode_gate(mode: SystemMode, kill: bool, action: Action, passes: bool) -> None:
    context = make_context(mode=mode, kill_switch_engaged=kill)
    assert result_for("SK-023", make_proposal(action=action), context).passed is passes


def test_040_sk024_unknown_candidate_id_is_rejected() -> None:
    """The hallucinated-instrument guard."""
    context = make_context(supplied_candidate_ids=frozenset({"cand_something_else"}))
    assert result_for("SK-024", make_proposal(), context).passed is False


def test_040_sk025_unreadable_account_rejects() -> None:
    context = make_context(account=make_account(read_ok=False))
    assert result_for("SK-025", make_proposal(), context).passed is False


# ---------------------------------------------------------------------------
# TEST-041 — fail closed, one rule at a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", RULES, ids=lambda s: s.rule_id)
def test_041_a_raising_rule_fails_closed(spec: RuleSpec, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rule, individually sabotaged, must reject rather than pass."""

    def explode(*_args: object, **_kwargs: object) -> RuleResult:
        raise RuntimeError(f"injected fault in {spec.rule_id}")

    patched = tuple(replace(s, evaluate=explode) if s.rule_id == spec.rule_id else s for s in RULES)
    monkeypatch.setattr(kernel, "RULES", patched)

    verdict = kernel.evaluate(
        make_proposal(), requested_contracts=1, context=make_context(), secret=SECRET
    )

    assert verdict.verdict is Decision.REJECT
    assert FAIL_CLOSED in verdict.reject_reasons
    assert verdict.signature is None


def test_041_a_rule_returning_the_wrong_type_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = tuple(
        replace(s, evaluate=lambda *_: "looks fine to me") if s.rule_id == "SK-003" else s
        for s in RULES
    )
    monkeypatch.setattr(kernel, "RULES", patched)

    verdict = kernel.evaluate(
        make_proposal(), requested_contracts=1, context=make_context(), secret=SECRET
    )
    assert verdict.verdict is Decision.REJECT
    assert FAIL_CLOSED in verdict.reject_reasons


def test_041_kernel_level_failure_still_returns_a_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-062: even a broken Kernel refuses; it never raises into the cycle."""

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the rule table itself is unusable")

    monkeypatch.setattr(kernel, "_evaluate", explode)
    verdict = kernel.evaluate(
        make_proposal(), requested_contracts=1, context=make_context(), secret=SECRET
    )

    assert verdict.verdict is Decision.REJECT
    assert verdict.reject_reasons == (FAIL_CLOSED,)
    assert verdict.signature is None


# ---------------------------------------------------------------------------
# TEST-042 — every reason, not the first reason
# ---------------------------------------------------------------------------


def test_042_all_failing_rules_are_reported() -> None:
    """SK-P3: the ledger has to show every reason a trade died."""
    proposal = make_proposal(max_loss="90000.00", dte=2, edge_ratio="-0.5", liquidity_score="0.1")
    verdict = kernel.evaluate(
        proposal, requested_contracts=1, context=make_context(), secret=SECRET
    )

    reasons = set(verdict.reject_reasons)
    assert {
        "POSITION_LOSS_LIMIT",
        "MAX_PORTFOLIO_RISK",
        "DTE_TOO_SHORT",
        "INSUFFICIENT_EDGE",
        "ILLIQUID",
    } <= reasons, kernel.explain(verdict)

    # And every rule is present in the record, passing ones included.
    assert len(verdict.rules) == len(RULES) + 1  # + SK-020, appended after sizing


# ---------------------------------------------------------------------------
# TEST-043 — SK-000, the privileged close
# ---------------------------------------------------------------------------


def test_043_a_close_is_approved_despite_capital_exhaustion() -> None:
    """Capital is fully deployed and the day is at its loss limit.

    Opening is impossible. Closing must still be permitted, or the system
    cannot reduce the very risk that exhausted it.
    """
    exhausted = make_context(
        open_policies=tuple(
            make_policy(policy_id=f"pol_{i}", max_loss="12000.00") for i in range(5)
        ),
        account=make_account(),
    )

    opening = kernel.evaluate(
        make_proposal(), requested_contracts=1, context=exhausted, secret=SECRET
    )
    assert opening.verdict is Decision.REJECT
    assert "MAX_DEPLOYED" in opening.reject_reasons

    closing = kernel.evaluate(
        make_proposal(action=Action.CLOSE),
        requested_contracts=1,
        context=exhausted,
        secret=SECRET,
    )
    assert closing.verdict is Decision.APPROVE, kernel.explain(closing)
    assert closing.approved_contracts == 1


def test_043_a_close_still_obeys_the_safety_rules() -> None:
    """SK-000 exempts capital rules only. A closed market still blocks a close."""
    verdict = kernel.evaluate(
        make_proposal(action=Action.CLOSE),
        requested_contracts=1,
        context=make_context(market_open=False),
        secret=SECRET,
    )
    assert verdict.verdict is Decision.REJECT
    assert "MARKET_CLOSED" in verdict.reject_reasons


# ---------------------------------------------------------------------------
# TEST-044 — soft rules reduce size, never reject
# ---------------------------------------------------------------------------


def test_044_soft_failures_halve_the_size_without_rejecting() -> None:
    # Assignment cost binds at 3 contracts on the default account, which would
    # mask the halving. Give the book room so the soft factor is what moves.
    roomy = {"account": make_account(buying_power="2000000.00")}

    healthy = kernel.evaluate(
        make_proposal(), requested_contracts=8, context=make_context(**roomy), secret=SECRET
    )
    assert healthy.verdict is Decision.APPROVE
    assert healthy.approved_contracts == 8

    one_soft = kernel.evaluate(
        make_proposal(),
        requested_contracts=8,
        context=make_context(portfolio_net_delta="500", **roomy),
        secret=SECRET,
    )
    assert one_soft.verdict is Decision.APPROVE
    assert one_soft.approved_contracts == 4

    both_soft = kernel.evaluate(
        make_proposal(),
        requested_contracts=8,
        context=make_context(portfolio_net_delta="500", portfolio_net_vega="500", **roomy),
        secret=SECRET,
    )
    assert both_soft.verdict is Decision.APPROVE
    assert both_soft.approved_contracts == 2


def test_044_soft_reduction_below_one_contract_rejects_on_sk020() -> None:
    """The size floor is where a soft rule can still end in a rejection."""
    verdict = kernel.evaluate(
        make_proposal(),
        requested_contracts=1,
        context=make_context(portfolio_net_delta="500", portfolio_net_vega="500"),
        secret=SECRET,
    )
    assert verdict.verdict is Decision.REJECT
    assert "SIZE_FLOOR" in verdict.reject_reasons


def test_044_requesting_zero_contracts_rejects_on_the_size_floor() -> None:
    verdict = kernel.evaluate(
        make_proposal(), requested_contracts=0, context=make_context(), secret=SECRET
    )
    assert verdict.verdict is Decision.REJECT
    assert "SIZE_FLOOR" in verdict.reject_reasons


# ---------------------------------------------------------------------------
# Sizing (§15.5)
# ---------------------------------------------------------------------------


def test_sizing_takes_the_minimum_and_names_the_binding_constraint() -> None:
    """$3,000 position-risk room over a $150 max loss permits 20 contracts."""
    sizing = kernel.compute_sizing(make_proposal(), make_context(), kernel.DEFAULT_LIMITS)
    assert sizing.by_position_risk == 20
    assert sizing.permitted == min(
        sizing.by_position_risk,
        sizing.by_portfolio_room,
        sizing.by_concentration,
        sizing.by_deployed_capital,
        sizing.by_assignment_cost,
    )
    assert sizing.binding_constraint == "assignment_cost"  # $200k BP / $55k per contract


def test_sizing_caps_the_llm_request_rather_than_rejecting_it() -> None:
    """FR-046: the model's number is advisory. Asking for 500 is not an error."""
    verdict = kernel.evaluate(
        make_proposal(), requested_contracts=500, context=make_context(), secret=SECRET
    )
    assert verdict.verdict is Decision.APPROVE
    assert verdict.approved_contracts == 3  # assignment cost binds first


def test_sizing_returns_zero_when_the_room_is_negative() -> None:
    context = make_context(
        open_policies=(make_policy(max_loss="59000.00"),),
        account=make_account(nav="100000.00"),
    )
    sizing = kernel.compute_sizing(make_proposal(), context, kernel.DEFAULT_LIMITS)
    assert sizing.by_deployed_capital == 6
    assert sizing.permitted >= 0


def test_sizing_handles_a_zero_denominator_without_dividing_by_it() -> None:
    proposal = make_proposal(max_loss="0.00", capital_reserve="0.00")
    sizing = kernel.compute_sizing(proposal, make_context(), kernel.DEFAULT_LIMITS)
    assert sizing.by_position_risk == 0
    assert sizing.permitted == 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_explain_lists_every_rule_with_its_outcome() -> None:
    verdict = kernel.evaluate(
        make_proposal(dte=1), requested_contracts=1, context=make_context(), secret=SECRET
    )
    rendered = kernel.explain(verdict)
    assert "SK-011" in rendered
    assert "REJECT" in rendered
    assert rendered.count("\n") == len(verdict.rules)


def test_client_order_id_is_deterministic_from_the_proposal() -> None:
    proposal = make_proposal()
    assert client_order_id_for(proposal.proposal_hash) == client_order_id_for(
        proposal.proposal_hash
    )
    other = make_proposal(short_strike=545)
    assert client_order_id_for(proposal.proposal_hash) != client_order_id_for(other.proposal_hash)


def test_a_call_spread_leg_is_covered_by_expiry_alone() -> None:
    """SK-004's strike test is put-specific; calls are covered on expiry."""
    call_legs = (
        SpreadLeg(
            symbol="SPY260918C00600000",
            right=OptionRight.CALL,
            side=Side.SELL,
            strike=Decimal("600"),
            expiry=EXPIRY,
        ),
        SpreadLeg(
            symbol="SPY260918C00605000",
            right=OptionRight.CALL,
            side=Side.BUY,
            strike=Decimal("605"),
            expiry=EXPIRY,
        ),
    )
    assert result_for("SK-004", make_proposal(legs=call_legs), make_context()).passed is True


def test_sk000_records_that_the_exemption_applied() -> None:
    closing = result_for("SK-000", make_proposal(action=Action.CLOSE), make_context())
    assert closing.passed is True
    assert "SK-001" in closing.message

    opening = result_for("SK-000", make_proposal(), make_context())
    assert "all rules apply" in opening.message


def test_verdict_ttl_matches_the_configured_limit() -> None:
    verdict = kernel.evaluate(
        make_proposal(), requested_contracts=1, context=make_context(), secret=SECRET
    )
    assert (verdict.expires_at - verdict.issued_at).total_seconds() == 45
    assert verdict.issued_at == NOW


def test_expiry_date_helper_is_the_date_the_fixtures_claim() -> None:
    assert date(2026, 9, 18) == EXPIRY

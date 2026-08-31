"""Actuary tests — TEST-020 … TEST-026.

The Actuary computes; it never persuades. These tests pin the arithmetic to
hand-worked numbers, because a formula that is subtly wrong here is wrong in
every verdict, every reserve and every P&L figure downstream, and nothing later
in the pipeline would notice.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import D, make_quote, make_snapshot
from underwriter.actuary import formulas
from underwriter.actuary.engine import candidate_id_for, price_put_credit_spreads
from underwriter.actuary.thresholds import DEFAULT_THRESHOLDS, ActuaryThresholds
from underwriter.actuary.validation import DiscardReason, validate_quote
from underwriter.domain.market import OptionRight, Side
from underwriter.domain.money import MoneyError
from underwriter.domain.proposal import SpreadLeg

ACTUARY_DIR = pathlib.Path(__file__).resolve().parents[1] / "underwriter" / "actuary"

# The hand-worked example every golden assertion below refers to:
#
#   short put  550  bid 2.00  ask 2.10  delta -0.20
#   long  put  548  bid 1.40  ask 1.50  delta -0.15
#
#   width       = 550 - 548                = 2.00
#   net_credit  = 2.00 (short bid) - 1.50 (long ask) = 0.50
#   max_profit  = 0.50 x 100               = 50.00
#   max_loss    = (2.00 - 0.50) x 100      = 150.00
#   breakeven   = 550 - 0.50               = 549.50
#   c/w         = 0.50 / 2.00              = 0.25
#   EV          = 0.80 x 50 - 0.20 x 150   = 10.00
#   edge_ratio  = 10 / 150                 = 0.066667


# ---------------------------------------------------------------------------
# TEST-020 — no LLM, no network, reachable from the actuary package
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORTS = {
    "groq",
    "openai",
    "anthropic",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "aiohttp",
    "alpaca",
    "random",
}


@pytest.mark.parametrize("source", sorted(ACTUARY_DIR.glob("*.py")), ids=lambda p: p.name)
def test_020_actuary_imports_nothing_that_talks_to_the_world(source: pathlib.Path) -> None:
    """FR-020 and FR-027, asserted statically rather than trusted.

    `random` is in the forbidden set alongside the network clients: a random
    source would break determinism just as surely as a network call, and both
    would be invisible in a passing test suite.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    leaked = imported & FORBIDDEN_IMPORTS
    assert not leaked, f"{source.name} imports {leaked}"


def test_020_actuary_never_reads_the_clock() -> None:
    """Every time value comes from the snapshot, never from `now()`."""
    for source in ACTUARY_DIR.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow", "today"}:
                pytest.fail(f"{source.name} reads the clock via .{node.attr}()")


# ---------------------------------------------------------------------------
# TEST-022 and TEST-026 — golden numbers and conservative pricing
# ---------------------------------------------------------------------------


def test_026_credit_uses_the_short_bid_and_the_long_ask() -> None:
    """FR-024: assume the worse fill on both legs.

    Priced off the mids this spread would pay 0.60. Priced conservatively it
    pays 0.50, and every number downstream inherits that pessimism.
    """
    conservative = formulas.net_credit(D("2.00"), D("1.50"))
    assert conservative == D("0.50")

    optimistic_mid = D("2.05") - D("1.45")
    assert conservative < optimistic_mid


def test_022_golden_numbers_for_the_hand_worked_spread() -> None:
    width = formulas.spread_width(D(550), D(548))
    credit = formulas.net_credit(D("2.00"), D("1.50"))

    assert width == D("2.0000")
    assert credit == D("0.50")
    assert formulas.max_profit(credit) == D("50.00")
    assert formulas.max_loss(width, credit) == D("150.00")
    assert formulas.breakeven(D(550), credit) == D("549.5000")
    assert formulas.credit_to_width(credit, width) == D("0.250000")

    p_loss = formulas.loss_probability_proxy(D("-0.20"))
    assert p_loss == D("0.200000")

    ev = formulas.expected_value(p_loss, D("50.00"), D("150.00"))
    assert ev == D("10.00")
    assert formulas.edge_ratio(ev, D("150.00")) == D("0.066667")


def test_022_golden_liquidity_score() -> None:
    """0.5 x spread tightness + 0.25 x depth + 0.25 x open interest."""
    short = make_quote(bid="2.00", ask="2.10", bid_size=50, ask_size=50, open_interest=2000)
    score = formulas.liquidity_score(
        short,
        max_bid_ask_pct=D("0.15"),
        min_depth=D("10"),
        min_oi=D("500"),
    )
    assert score == D("0.837398")


def test_022_missing_open_interest_scores_a_neutral_half() -> None:
    neutral = formulas.liquidity_score(
        make_quote(open_interest=None),
        max_bid_ask_pct=D("0.15"),
        min_depth=D("10"),
        min_oi=D("500"),
    )
    full = formulas.liquidity_score(
        make_quote(open_interest=5000),
        max_bid_ask_pct=D("0.15"),
        min_depth=D("10"),
        min_oi=D("500"),
    )
    assert neutral < full
    assert full - neutral == D("0.125")  # a quarter of the score, halved


def test_022_position_greeks_are_signed_by_side() -> None:
    """A put credit spread is net long delta and net short vega."""
    legs = (
        SpreadLeg("S", OptionRight.PUT, Side.SELL, D(550), make_quote().expiry),
        SpreadLeg("L", OptionRight.PUT, Side.BUY, D(548), make_quote().expiry),
    )
    quotes = {
        "S": make_quote(symbol="S", delta="-0.20", vega="0.15"),
        "L": make_quote(symbol="L", delta="-0.15", vega="0.10"),
    }
    net_delta, net_vega = formulas.position_greeks(legs, quotes)

    assert net_delta == D("5.0000")  # +20 short put, -15 long put
    assert net_vega == D("-5.0000")  # -15 short put, +10 long put


# ---------------------------------------------------------------------------
# TEST-023 — the invariant that must hold for every valid spread
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("short_strike", [Decimal(s) for s in (100, 250, 400, 550, 700)])
@pytest.mark.parametrize("width_value", [Decimal(w) for w in (1, 2, 3, 5)])
@pytest.mark.parametrize("credit_fraction", ["0.10", "0.25", "0.40", "0.49"])
def test_023_max_loss_invariant(
    short_strike: Decimal, width_value: Decimal, credit_fraction: str
) -> None:
    """max_loss = (width - credit) x 100, and it is always positive."""
    long_strike = short_strike - width_value
    credit = (width_value * D(credit_fraction)).quantize(D("0.01"))

    width = formulas.spread_width(short_strike, long_strike)
    computed = formulas.max_loss(width, credit)

    assert computed == (width - credit) * 100
    assert computed > 0
    assert formulas.max_profit(credit) + computed == width * 100


def test_023_a_credit_at_or_above_the_width_is_refused() -> None:
    """Risk-free premium means a bad quote, not free money."""
    with pytest.raises(MoneyError, match="implausible"):
        formulas.max_loss(D("2.00"), D("2.00"))


def test_023_a_non_positive_width_is_refused() -> None:
    with pytest.raises(MoneyError, match="width must be positive"):
        formulas.spread_width(D(548), D(550))


def test_023_a_non_positive_credit_is_refused() -> None:
    with pytest.raises(MoneyError, match="credit must be positive"):
        formulas.net_credit(D("1.00"), D("1.50"))


def test_023_an_out_of_range_delta_is_refused() -> None:
    with pytest.raises(MoneyError, match="out of range"):
        formulas.loss_probability_proxy(D("-1.5"))


def test_023_greeks_are_never_estimated() -> None:
    """FR-004: absence is a discard reason, never an input to a guess."""
    legs = (SpreadLeg("S", OptionRight.PUT, Side.SELL, D(550), make_quote().expiry),)
    with pytest.raises(MoneyError, match="refusing to estimate"):
        formulas.position_greeks(legs, {"S": make_quote(symbol="S", delta=None)})


# ---------------------------------------------------------------------------
# TEST-024 — determinism
# ---------------------------------------------------------------------------


def test_024_the_same_snapshot_produces_identical_output_every_time() -> None:
    """FR-027, checked the way the SRS words it: 100 runs, byte-identical."""
    snapshot = make_snapshot()
    first = price_put_credit_spreads(snapshot)

    for _ in range(100):
        again = price_put_credit_spreads(snapshot)
        assert again == first

    assert len(first.proposals) == 1
    assert first.proposals[0].proposal_hash == first.proposals[0].proposal_hash


def test_024_candidate_ids_are_stable_and_snapshot_scoped() -> None:
    stable = candidate_id_for("SPY", "2026-09-18", D(550), D(548), "a" * 64)
    assert stable == candidate_id_for("SPY", "2026-09-18", D(550), D(548), "a" * 64)
    assert stable != candidate_id_for("SPY", "2026-09-18", D(550), D(548), "b" * 64)


def test_024_snapshot_hash_changes_when_any_quote_changes() -> None:
    baseline = make_snapshot().snapshot_hash
    moved = make_snapshot(
        quotes=(
            make_quote(symbol="SPY260918P00550000", strike=550, bid="2.01", ask="2.10"),
            make_quote(symbol="SPY260918P00548000", strike=548, bid="1.40", ask="1.50"),
        )
    ).snapshot_hash
    assert baseline != moved


# ---------------------------------------------------------------------------
# TEST-025 — bad inputs are discarded with the right reason, never priced
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"tradable": False}, DiscardReason.NOT_TRADABLE),
        ({"bid": "0.00"}, DiscardReason.BAD_QUOTE),
        ({"ask": "0.00"}, DiscardReason.BAD_QUOTE),
        ({"bid": "2.20", "ask": "2.10"}, DiscardReason.BAD_QUOTE),  # crossed
        ({"bid": "2.00", "ask": "2.60"}, DiscardReason.WIDE_SPREAD),
        ({"implied_volatility": None}, DiscardReason.MISSING_IV),
        ({"implied_volatility": "0"}, DiscardReason.MISSING_IV),
        ({"implied_volatility": "9.0"}, DiscardReason.MISSING_IV),
        ({"delta": None}, DiscardReason.MISSING_GREEKS),
        ({"delta": "-1.5"}, DiscardReason.MISSING_GREEKS),
        ({"vega": None}, DiscardReason.MISSING_GREEKS),
    ],
)
def test_025_validation_names_the_failing_step(
    kwargs: dict[str, object], reason: DiscardReason
) -> None:
    outcome = validate_quote(make_quote(**kwargs), DEFAULT_THRESHOLDS)  # type: ignore[arg-type]
    assert outcome is not None
    assert outcome.reason is reason


def test_025_a_healthy_quote_passes_validation() -> None:
    assert validate_quote(make_quote(), DEFAULT_THRESHOLDS) is None


def test_025_a_dead_quote_cannot_produce_a_midpoint() -> None:
    with pytest.raises(MoneyError, match="no midpoint"):
        _ = make_quote(bid="0.00", ask="0.00").mid


def test_025_bad_quotes_never_reach_the_proposal_set() -> None:
    """The whole point: a discarded candidate is never priced, only recorded."""
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="SPY260918P00550000", strike=550, delta=None),
            make_quote(symbol="SPY260918P00548000", strike=548, bid="1.40", ask="1.50"),
        )
    )
    result = price_put_credit_spreads(snapshot)

    assert result.is_empty
    assert any(d.reason is DiscardReason.MISSING_GREEKS for d in result.discards)


# ---------------------------------------------------------------------------
# Engine — enumeration and the pre-filter
# ---------------------------------------------------------------------------


def test_engine_prices_the_hand_worked_spread_end_to_end() -> None:
    result = price_put_credit_spreads(make_snapshot())
    assert len(result.proposals) == 1

    proposal = result.proposals[0]
    assert proposal.underlying == "SPY"
    assert proposal.short_strike == D(550)
    assert proposal.long_strike == D(548)
    assert proposal.net_credit == D("0.50")
    assert proposal.max_profit == D("50.00")
    assert proposal.max_loss == D("150.00")
    assert proposal.capital_reserve == proposal.max_loss  # SK-002 holds by construction
    assert proposal.breakeven == D("549.5000")
    assert proposal.edge_ratio == D("0.066667")
    assert proposal.liquidity_score == D("0.770115")  # the worse leg, not the better
    assert proposal.dte == 17
    assert proposal.greeks_complete is True


def test_engine_skips_underlyings_outside_the_universe() -> None:
    result = price_put_credit_spreads(make_snapshot(), universe=("QQQ",))
    assert result.is_empty
    assert result.discards == ()


def test_engine_ignores_calls() -> None:
    calls = make_snapshot(
        quotes=(
            make_quote(symbol="C1", strike=550, right=OptionRight.CALL),
            make_quote(symbol="C2", strike=548, right=OptionRight.CALL),
        )
    )
    assert price_put_credit_spreads(calls).is_empty


@pytest.mark.parametrize(("days", "kept"), [(6, False), (7, True), (21, True), (22, False)])
def test_engine_enforces_the_dte_window(days: int, kept: bool) -> None:
    as_of = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)
    expiry = (as_of + timedelta(days=days)).date()
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, expiry=expiry),
            make_quote(symbol="B", strike=548, expiry=expiry, bid="1.40", ask="1.50"),
        ),
        as_of=as_of,
    )
    result = price_put_credit_spreads(snapshot)

    assert (not result.is_empty) is kept
    if not kept:
        assert any(d.reason is DiscardReason.DTE_OUT_OF_WINDOW for d in result.discards)


def test_engine_skips_a_short_leg_outside_the_delta_band() -> None:
    """Delta 0.40 is too close to the money for this strategy (§15.3)."""
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, delta="-0.40"),
            make_quote(symbol="B", strike=548, delta="-0.35", bid="1.40", ask="1.50"),
        )
    )
    assert price_put_credit_spreads(snapshot).is_empty


def test_engine_skips_widths_outside_the_configured_band() -> None:
    """A 10-point width is outside width_max, so the pair is never formed."""
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550),
            make_quote(symbol="B", strike=540, bid="1.40", ask="1.50", delta="-0.13"),
        )
    )
    assert price_put_credit_spreads(snapshot).is_empty


@pytest.mark.parametrize(
    ("long_bid", "long_ask", "reason"),
    [
        ("1.80", "1.90", DiscardReason.CREDIT_TO_WIDTH_LOW),  # credit 0.10 of a 2.00 width
        ("0.90", "1.00", DiscardReason.CREDIT_TO_WIDTH_HIGH),  # credit 1.00 of a 2.00 width
    ],
)
def test_engine_discards_on_credit_to_width(
    long_bid: str, long_ask: str, reason: DiscardReason
) -> None:
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, bid="2.00", ask="2.10"),
            make_quote(symbol="B", strike=548, bid=long_bid, ask=long_ask, delta="-0.15"),
        )
    )
    result = price_put_credit_spreads(snapshot)
    assert result.is_empty
    assert any(d.reason is reason for d in result.discards)


def test_engine_discards_on_insufficient_edge() -> None:
    """Delta 0.28 against a 0.20 credit-to-width leaves no expected value."""
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, bid="1.90", ask="2.00", delta="-0.28"),
            make_quote(symbol="B", strike=548, bid="1.40", ask="1.50", delta="-0.13"),
        )
    )
    result = price_put_credit_spreads(snapshot)
    assert result.is_empty
    assert any(d.reason is DiscardReason.INSUFFICIENT_EDGE for d in result.discards)


def test_engine_discards_an_illiquid_spread() -> None:
    thin = ActuaryThresholds(min_liquidity_score=Decimal("0.95"))
    result = price_put_credit_spreads(make_snapshot(), thresholds=thin)
    assert result.is_empty
    assert any(d.reason is DiscardReason.ILLIQUID for d in result.discards)


def test_engine_records_a_math_error_instead_of_raising() -> None:
    """§11.2: the Actuary never raises into the cycle.

    A 548 long ask above the 550 short bid inverts the credit, which is a data
    error rather than a tradable spread.
    """
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, bid="1.00", ask="1.10"),
            make_quote(symbol="B", strike=548, bid="1.40", ask="1.50", delta="-0.15"),
        )
    )
    result = price_put_credit_spreads(snapshot)
    assert result.is_empty
    assert any(d.reason is DiscardReason.ACTUARY_MATH_ERROR for d in result.discards)


def test_engine_orders_proposals_by_edge_then_id() -> None:
    """Best edge first, so the prompt shows the model its strongest candidates."""
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, bid="2.00", ask="2.10", delta="-0.20"),
            make_quote(symbol="B", strike=548, bid="1.40", ask="1.50", delta="-0.15"),
            make_quote(symbol="C", strike=546, bid="0.85", ask="0.95", delta="-0.12"),
        )
    )
    result = price_put_credit_spreads(snapshot)

    # Three pairs survive: 550/548, 550/546 and 548/546.
    assert len(result.proposals) == 3

    edges = [p.edge_ratio for p in result.proposals]
    assert edges == sorted(edges, reverse=True)

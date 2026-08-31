"""Normative pricing formulas — SRS §11.2, verbatim.

Every number the system trades on originates here. Nothing in this module
touches the network, the clock, or a random source (FR-027), and no function
here knows what an LLM is (FR-020).

Sign conventions, stated once:
  * A put's delta is negative. Selling it produces positive position delta.
  * `Side.sign` is +1 long / -1 short, so position exposure is always
    `sign * greek * 100 * contracts`.
"""

from __future__ import annotations

from decimal import Decimal

from underwriter.domain.market import ContractQuote, Side
from underwriter.domain.money import (
    CONTRACT_MULTIPLIER,
    ONE,
    ZERO,
    MoneyError,
    q_credit,
    q_loss,
    q_per_share,
    q_ratio,
    safe_div,
)
from underwriter.domain.proposal import SpreadLeg


def spread_width(short_strike: Decimal, long_strike: Decimal) -> Decimal:
    """`width = Ks - Kl`, with `Ks > Kl` enforced."""
    width = short_strike - long_strike
    if width <= ZERO:
        raise MoneyError(f"width must be positive: Ks={short_strike} Kl={long_strike}")
    return q_per_share(width)


def net_credit(short_bid: Decimal, long_ask: Decimal) -> Decimal:
    """`net_credit = short.bid - long.ask`.

    FR-024: the conservative side of both spreads. We assume we sell at the bid
    and buy at the ask — the worse fill on each leg — so a candidate that looks
    marginal here is never better than it appears.
    """
    credit = short_bid - long_ask
    if credit <= ZERO:
        raise MoneyError(f"credit must be positive: short_bid={short_bid} long_ask={long_ask}")
    return q_credit(credit)


def max_profit(credit: Decimal) -> Decimal:
    """`max_profit = net_credit * 100`."""
    return q_credit(credit * CONTRACT_MULTIPLIER)


def max_loss(width: Decimal, credit: Decimal) -> Decimal:
    """`max_loss = (width - net_credit) * 100`.

    A credit at or above the width would imply a risk-free spread, which means
    the quote is wrong, not that we found free money.
    """
    if credit >= width:
        raise MoneyError(f"credit {credit} >= width {width}: implausible quote")
    return q_loss((width - credit) * CONTRACT_MULTIPLIER)


def breakeven(short_strike: Decimal, credit: Decimal) -> Decimal:
    """`breakeven = Ks - net_credit`."""
    return q_per_share(short_strike - credit)


def credit_to_width(credit: Decimal, width: Decimal) -> Decimal:
    """`credit_to_width = net_credit / width`."""
    return q_ratio(safe_div(credit, width, field="credit_to_width"))


def loss_probability_proxy(short_delta: Decimal) -> Decimal:
    """`p_loss_proxy = abs(short_put.delta)`.

    NG-02: delta is a risk-neutral approximation of P(finish ITM), not a
    real-world probability, and it ignores the partial-loss region between the
    breakeven and the short strike. Used because it is free, standard and
    consistent. The UI labels it "delta-implied, approximate".
    """
    p_loss = abs(short_delta)
    if not (ZERO <= p_loss <= ONE):
        raise MoneyError(f"delta-implied probability out of range: {p_loss}")
    return q_ratio(p_loss)


def expected_value(p_loss: Decimal, gross_max_profit: Decimal, gross_max_loss: Decimal) -> Decimal:
    """`expected_value = (1 - p_loss) * max_profit - p_loss * max_loss`.

    Deliberately pessimistic: the losing branch assumes the *full* max loss,
    ignoring the partial-loss region. A candidate that clears MIN_EDGE_RATIO
    under this model has real room.
    """
    p_profit = ONE - p_loss
    return q_credit(p_profit * gross_max_profit - p_loss * gross_max_loss)


def edge_ratio(ev: Decimal, gross_max_loss: Decimal) -> Decimal:
    """`edge_ratio = expected_value / max_loss` — normalised edge per unit risked."""
    return q_ratio(safe_div(ev, gross_max_loss, field="edge_ratio"))


def liquidity_score(
    quote: ContractQuote, *, max_bid_ask_pct: Decimal, min_depth: Decimal, min_oi: Decimal
) -> Decimal:
    """Composite 0-1 liquidity measure, higher better (§11.2).

    Weighted 50% spread tightness, 25% quoted depth, 25% open interest.
    Missing open interest scores 0.5 — neither rewarded nor treated as absent
    liquidity, since Alpaca omits it more often than the strike is illiquid.
    """
    spread_component = ONE - min(ONE, safe_div(quote.spread_pct, max_bid_ask_pct, field="spread"))
    depth_component = min(ONE, safe_div(Decimal(min(quote.bid_size, quote.ask_size)), min_depth))
    if quote.open_interest is None:
        oi_component = Decimal("0.5")
    else:
        oi_component = min(ONE, safe_div(Decimal(quote.open_interest), min_oi))

    score = (
        Decimal("0.5") * spread_component
        + Decimal("0.25") * depth_component
        + Decimal("0.25") * oi_component
    )
    return q_ratio(score)


def position_greeks(
    legs: tuple[SpreadLeg, ...], quotes: dict[str, ContractQuote]
) -> tuple[Decimal, Decimal]:
    """Signed (delta, vega) exposure for one spread, in share-equivalents.

    A put credit spread comes out net long delta and net short vega, which is
    what SK-008 and SK-009 are measuring across the book.
    """
    net_delta = ZERO
    net_vega = ZERO
    for leg in legs:
        quote = quotes.get(leg.symbol)
        if quote is None or quote.delta is None or quote.vega is None:
            raise MoneyError(f"{leg.symbol}: greeks unavailable, refusing to estimate (FR-004)")
        sign = Decimal(Side(leg.side).sign * leg.ratio_qty)
        net_delta += sign * quote.delta * CONTRACT_MULTIPLIER
        net_vega += sign * quote.vega * CONTRACT_MULTIPLIER
    return q_per_share(net_delta), q_per_share(net_vega)

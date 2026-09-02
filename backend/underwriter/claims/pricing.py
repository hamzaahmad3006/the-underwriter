"""What it would cost to close an open policy right now — FR-101, FR-102.

Without this the Claims Desk can only act on the one exit that needs no price:
force flat. The profit target and the stop loss both compare a live cost to the
opening credit, so a desk that cannot price its own book can never take a
profit and can never stop a loss — it can only run to expiry.

**Conservative in the exit direction.** Closing a put credit spread means
buying the short leg back and selling the long one, so the cost assumes the
worse fill on both: pay the short's *ask*, receive the long's *bid*. That
overstates what it costs to get out, which makes the profit target harder to
hit and the stop easier — both erring toward closing sooner. Entry pricing errs
the other way for the same reason: each side assumes the fill it would least
like.

A position that cannot be priced returns `None` rather than a guess. The Claims
Desk escalates on that, because not knowing is not the same as knowing it is
fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from underwriter.data.ports import MarketDataSource, RawContract
from underwriter.domain.money import ZERO


@dataclass(frozen=True, slots=True)
class ExitQuote:
    """The live cost to close one spread, and where it came from."""

    cost_to_close: Decimal | None
    underlying_price: Decimal | None
    detail: str

    @property
    def priceable(self) -> bool:
        return self.cost_to_close is not None


def cost_to_close(short: RawContract | None, long: RawContract | None) -> Decimal | None:
    """Buy the short back at its ask, sell the long at its bid.

    Both legs are required. Pricing half a spread would produce a number that
    looks like a cost and is not one.
    """
    if short is None or long is None:
        return None
    if short.ask is None or long.bid is None:
        return None
    if short.ask <= ZERO:
        return None

    debit = short.ask - long.bid
    # A negative debit would mean being paid to close, which happens only when
    # a quote is wrong. Floor at zero rather than report a credit that is not
    # there; the Claims Desk would read a negative cost as a free profit target.
    return max(ZERO, debit)


def price_position(
    source: MarketDataSource,
    *,
    underlying: str,
    expiry: date,
    short_symbol: str,
    long_symbol: str,
) -> ExitQuote:
    """Fetch the two legs and price the exit. Never raises into the cycle."""
    try:
        # A one-day window: this policy's expiry and nothing else. Fetching the
        # whole DTE range would pull hundreds of contracts to find two.
        contracts = source.get_option_chain(underlying, expiry_from=expiry, expiry_to=expiry)
    except Exception as exc:
        return ExitQuote(None, None, f"chain fetch failed: {type(exc).__name__}: {exc}")

    by_symbol = {contract.symbol: contract for contract in contracts}
    short = by_symbol.get(short_symbol)
    long = by_symbol.get(long_symbol)

    if short is None or long is None:
        missing = [s for s, c in ((short_symbol, short), (long_symbol, long)) if c is None]
        return ExitQuote(None, None, f"not quoted: {', '.join(missing)}")

    debit = cost_to_close(short, long)
    if debit is None:
        return ExitQuote(None, None, f"{short_symbol} has no usable two-sided quote")

    return ExitQuote(
        cost_to_close=debit,
        underlying_price=None,
        detail=f"short ask {short.ask}, long bid {long.bid}",
    )


def underlying_price(
    source: MarketDataSource, underlying: str, *, lookback: int = 2
) -> Decimal | None:
    """Latest close, for the short-strike breach check (FR-104).

    The close rather than a live quote: breach is a persistent condition, not a
    tick, and SK-013 escalates on it rather than trading off it.
    """
    try:
        closes = source.get_daily_closes(underlying, lookback_days=lookback)
    except Exception:
        return None
    return closes[-1] if closes else None

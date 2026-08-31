"""Market data values.

A `ContractQuote` is what the data layer produces and the Actuary consumes.
It carries `fetched_at` on every quote because SK-018 rejects on data age and
UI-002 forbids showing a number without its provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from underwriter.domain.hashing import sha256_of
from underwriter.domain.money import ZERO, MoneyError, safe_div


class OptionRight(StrEnum):
    PUT = "PUT"
    CALL = "CALL"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 long, -1 short. Used to sign Greeks into position exposure."""
        return 1 if self is Side.BUY else -1


@dataclass(frozen=True, slots=True)
class ContractQuote:
    """One option contract as quoted at `fetched_at`.

    Greeks are optional at this layer because Alpaca omits them (notably at
    0DTE). Absence is a discard reason, never something to estimate — FR-004.
    """

    symbol: str
    underlying: str
    right: OptionRight
    strike: Decimal
    expiry: date
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    fetched_at: datetime
    tradable: bool = True
    open_interest: int | None = None
    implied_volatility: Decimal | None = None
    delta: Decimal | None = None
    vega: Decimal | None = None

    @property
    def mid(self) -> Decimal:
        """Midpoint. Raises rather than returning zero on a dead quote."""
        total = self.bid + self.ask
        if total <= ZERO:
            raise MoneyError(f"{self.symbol}: no midpoint from bid={self.bid} ask={self.ask}")
        return total / Decimal("2")

    @property
    def spread_pct(self) -> Decimal:
        """Bid/ask spread as a fraction of mid — the SK-015 measure."""
        return safe_div(self.ask - self.bid, self.mid, field=f"{self.symbol}.spread_pct")

    def dte(self, as_of: date) -> int:
        """Calendar days to expiry. Negative once expired."""
        return (self.expiry - as_of).days


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """The immutable input to one underwriting cycle.

    FR-027: the same snapshot must produce the same proposals byte-for-byte,
    which is only checkable because the snapshot itself hashes stably.
    """

    as_of: datetime
    underlying_prices: dict[str, Decimal]
    quotes: tuple[ContractQuote, ...]

    @property
    def snapshot_hash(self) -> str:
        return sha256_of(
            {
                "as_of": self.as_of,
                "underlying_prices": self.underlying_prices,
                "quotes": [
                    {
                        "symbol": q.symbol,
                        "bid": q.bid,
                        "ask": q.ask,
                        "bid_size": q.bid_size,
                        "ask_size": q.ask_size,
                        "open_interest": q.open_interest,
                        "implied_volatility": q.implied_volatility,
                        "delta": q.delta,
                        "vega": q.vega,
                        "tradable": q.tradable,
                        "fetched_at": q.fetched_at,
                    }
                    for q in self.quotes
                ],
            }
        )

    def quote(self, symbol: str) -> ContractQuote | None:
        for q in self.quotes:
            if q.symbol == symbol:
                return q
        return None

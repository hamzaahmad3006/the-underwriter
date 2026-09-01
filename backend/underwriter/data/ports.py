"""What the data layer needs from a broker, stated as a protocol.

Everything above this file depends on these three methods, not on alpaca-py.
That is what lets the whole pipeline — chain fetch, validation, pricing,
adjudication — run in a test with no network and no credentials, which is the
only way FR-027's determinism claim is checkable at all.

`alpaca_source.py` is the one implementation that imports alpaca.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SessionState:
    """The market clock, reduced to what SK-017 actually adjudicates (FR-001)."""

    as_of: datetime
    is_open: bool
    minutes_since_open: int
    minutes_to_close: int

    def inside_blackout(self, open_min: int, close_min: int) -> bool:
        """Entries are refused near either bell, where pricing is unstable."""
        return self.minutes_since_open < open_min or self.minutes_to_close < close_min


@dataclass(frozen=True, slots=True)
class RawContract:
    """One option contract as the broker describes it, before validation.

    Deliberately permissive: every optional field is a thing Alpaca omits in
    practice, and absence has to survive as far as the validation pipeline so
    it can be recorded as a discard reason rather than crashing the fetch.
    """

    symbol: str
    underlying: str
    right: str
    strike: Decimal
    expiry: date

    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: int = 0
    ask_size: int = 0
    quote_at: datetime | None = None

    implied_volatility: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None

    open_interest: int | None = None
    tradable: bool = True


class MarketDataSource(Protocol):
    """The broker surface the data layer is allowed to touch. Reads only."""

    def get_session(self) -> SessionState:
        """FR-001 — the market clock, before every cycle."""
        ...

    def get_option_chain(
        self, underlying: str, *, expiry_from: date, expiry_to: date
    ) -> list[RawContract]:
        """FR-002, FR-003, FR-010 — puts in the DTE window, with Greeks."""
        ...

    def get_daily_closes(self, underlying: str, *, lookback_days: int) -> list[Decimal]:
        """FR-006 — daily closes, newest last, for realised volatility."""
        ...

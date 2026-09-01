"""Broker payloads to domain values.

Every number crosses this boundary through `to_decimal`, which routes floats
via `str` so Alpaca's binary representation never enters the arithmetic
(NFR-013). A value that will not convert becomes `None` and is discarded
downstream — this layer never guesses (FR-004).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from underwriter.data.ports import RawContract
from underwriter.domain.market import ContractQuote, OptionRight
from underwriter.domain.money import MoneyError, to_decimal


def optional_decimal(value: object, field: str) -> Decimal | None:
    """Convert, or return None. Absence and garbage are the same answer here."""
    if value is None:
        return None
    try:
        return to_decimal(value, field=field)
    except MoneyError:
        return None


def to_contract_quote(
    raw: RawContract, *, fetched_at: datetime, source: str = "rest"
) -> ContractQuote | None:
    """Build a domain quote, or None when the contract cannot be one.

    A missing bid or ask is not an error to raise: the chain is full of strikes
    nobody is quoting. It simply is not a tradable contract, so it never
    becomes a `ContractQuote` at all.
    """
    if raw.bid is None or raw.ask is None:
        return None

    try:
        right = OptionRight(raw.right.upper())
    except ValueError:
        return None

    return ContractQuote(
        symbol=raw.symbol,
        underlying=raw.underlying,
        right=right,
        strike=raw.strike,
        expiry=raw.expiry,
        bid=raw.bid,
        ask=raw.ask,
        bid_size=max(raw.bid_size, 0),
        ask_size=max(raw.ask_size, 0),
        fetched_at=raw.quote_at or fetched_at,
        tradable=raw.tradable,
        open_interest=raw.open_interest,
        implied_volatility=raw.implied_volatility,
        delta=raw.delta,
        vega=raw.vega,
        source=source,
    )

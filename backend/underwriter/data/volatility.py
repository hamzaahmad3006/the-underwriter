"""Realised volatility and IV rank — FR-006, FR-007.

FR-007 is unusual in that it *names its own fallback*: with too little IV
history, use the IV/RV ratio and record which measure was used. That honesty
matters here because ALP-022 means we start the event with no IV history at
all — Alpaca publishes no historical chain snapshots, so IV rank can only be
built forward from our own snapshots. On day one, every underlying falls back.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from underwriter.domain.money import ZERO, MoneyError, q_ratio, safe_div

TRADING_DAYS_PER_YEAR = Decimal("252")
MIN_CLOSES_FOR_RV = 3
MIN_IV_HISTORY_FOR_RANK = 20


class VolatilityMeasure(StrEnum):
    """Which measure produced the number. Recorded per FR-007."""

    IV_RANK = "IV_RANK"
    IV_RV_RATIO = "IV_RV_RATIO"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class VolatilityContext:
    """One underlying's volatility picture for this cycle."""

    underlying: str
    realized_vol: Decimal | None
    implied_vol: Decimal | None
    iv_rank: Decimal | None
    measure: VolatilityMeasure
    detail: str


def _sqrt(value: Decimal) -> Decimal:
    """Decimal square root. Kept exact rather than via float (NFR-013)."""
    return value.sqrt()


def realized_volatility(closes: list[Decimal]) -> Decimal | None:
    """Annualised close-to-close volatility over the supplied window.

    Log returns would be the textbook choice; simple returns are used because
    Decimal has no logarithm and the difference over a 20-day window on a
    liquid index ETF is immaterial next to the IV it is compared against.
    Returning None on thin data is deliberate — a volatility estimate from
    three points is worse than admitting we do not have one.
    """
    if len(closes) < MIN_CLOSES_FOR_RV:
        return None

    returns: list[Decimal] = []
    for previous, current in pairwise(closes):
        if previous <= ZERO:
            return None
        returns.append((current - previous) / previous)

    if len(returns) < 2:
        return None

    mean = sum(returns, ZERO) / Decimal(len(returns))
    variance = sum(((r - mean) ** 2 for r in returns), ZERO) / Decimal(len(returns) - 1)
    if variance < ZERO:
        return None

    return q_ratio(_sqrt(variance) * _sqrt(TRADING_DAYS_PER_YEAR))


def iv_rank(current_iv: Decimal, history: list[Decimal]) -> Decimal | None:
    """Where today's IV sits in its own range, 0 to 100.

    Needs a real spread to mean anything: a flat history would put every
    reading at either end of a zero-width range.
    """
    if len(history) < MIN_IV_HISTORY_FOR_RANK:
        return None

    low, high = min(history), max(history)
    if high <= low:
        return None

    ratio = safe_div(current_iv - low, high - low, field="iv_rank")
    clamped = max(ZERO, min(Decimal("1"), ratio))
    return q_ratio(clamped * Decimal("100"))


def build_context(
    underlying: str,
    *,
    closes: list[Decimal],
    current_iv: Decimal | None,
    iv_history: list[Decimal],
) -> VolatilityContext:
    """FR-007: prefer IV rank, fall back to IV/RV, and say which was used."""
    rv = realized_volatility(closes)

    if current_iv is None:
        return VolatilityContext(
            underlying, rv, None, None, VolatilityMeasure.UNAVAILABLE, "no implied volatility"
        )

    rank = iv_rank(current_iv, iv_history)
    if rank is not None:
        return VolatilityContext(
            underlying,
            rv,
            current_iv,
            rank,
            VolatilityMeasure.IV_RANK,
            f"{len(iv_history)} observations",
        )

    if rv is None or rv <= ZERO:
        return VolatilityContext(
            underlying,
            rv,
            current_iv,
            None,
            VolatilityMeasure.UNAVAILABLE,
            "insufficient IV history and no realised volatility to fall back on",
        )

    try:
        ratio = q_ratio(safe_div(current_iv, rv, field="iv_rv_ratio"))
    except MoneyError:
        return VolatilityContext(
            underlying, rv, current_iv, None, VolatilityMeasure.UNAVAILABLE, "iv/rv undefined"
        )

    # Mapped onto the same 0-100 scale so one config threshold reads both:
    # IV at 2x realised is treated as the top of the range.
    scaled = max(ZERO, min(Decimal("100"), (ratio - Decimal("0.5")) * Decimal("66.67")))
    return VolatilityContext(
        underlying,
        rv,
        current_iv,
        q_ratio(scaled),
        VolatilityMeasure.IV_RV_RATIO,
        f"iv/rv={ratio}, only {len(iv_history)} IV observations (ALP-022)",
    )

"""Per-contract validation — §11.1 pipeline, steps 3 to 8.

A discarded candidate is a *successful* outcome with a recorded reason
(FR-023). Nothing here estimates a missing value: FR-004 forbids inventing
Greeks, and G-08 forbids holding risk the system cannot measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from underwriter.actuary.thresholds import ActuaryThresholds
from underwriter.domain.market import ContractQuote
from underwriter.domain.money import ONE, ZERO, MoneyError


class DiscardReason(StrEnum):
    """Why a candidate never reached the LLM. Persisted verbatim (DB-005)."""

    # §11.1 per-contract pipeline
    BAD_QUOTE = "BAD_QUOTE"
    WIDE_SPREAD = "WIDE_SPREAD"
    MISSING_IV = "MISSING_IV"
    MISSING_GREEKS = "MISSING_GREEKS"
    NOT_TRADABLE = "NOT_TRADABLE"
    # §11.2 actuary pre-filter
    DTE_OUT_OF_WINDOW = "DTE_OUT_OF_WINDOW"
    WIDTH_OUT_OF_BAND = "WIDTH_OUT_OF_BAND"
    DELTA_OUT_OF_BAND = "DELTA_OUT_OF_BAND"
    CREDIT_TO_WIDTH_LOW = "CREDIT_TO_WIDTH_LOW"
    CREDIT_TO_WIDTH_HIGH = "CREDIT_TO_WIDTH_HIGH"
    INSUFFICIENT_EDGE = "INSUFFICIENT_EDGE"
    ILLIQUID = "ILLIQUID"
    ACTUARY_MATH_ERROR = "ACTUARY_MATH_ERROR"


@dataclass(frozen=True, slots=True)
class Discard:
    candidate_id: str
    reason: DiscardReason
    detail: str


def validate_quote(quote: ContractQuote, thresholds: ActuaryThresholds) -> Discard | None:
    """Steps 3 to 6 and 8. Returns the failing reason, or None if usable.

    Step 1 (market hours), step 2 (chain non-empty) and step 7 (data age) abort
    the whole cycle rather than one contract, so they live upstream — step 7 is
    also re-adjudicated by SK-018.
    """
    if not quote.tradable:
        return Discard(quote.symbol, DiscardReason.NOT_TRADABLE, "tradable=false")

    # Finiteness first: comparing a NaN raises InvalidOperation rather than
    # returning False, so an unchecked NaN would take down the whole cycle.
    if not quote.bid.is_finite() or not quote.ask.is_finite():
        return Discard(
            quote.symbol,
            DiscardReason.BAD_QUOTE,
            f"non-finite quote: bid={quote.bid} ask={quote.ask}",
        )

    if quote.bid <= ZERO or quote.ask <= ZERO or quote.ask <= quote.bid:
        return Discard(
            quote.symbol,
            DiscardReason.BAD_QUOTE,
            f"bid={quote.bid} ask={quote.ask}",
        )

    try:
        spread_pct = quote.spread_pct
    except MoneyError as exc:
        return Discard(quote.symbol, DiscardReason.BAD_QUOTE, str(exc))

    if spread_pct > thresholds.max_bid_ask_pct:
        return Discard(
            quote.symbol,
            DiscardReason.WIDE_SPREAD,
            f"spread_pct={spread_pct} > {thresholds.max_bid_ask_pct}",
        )

    iv = quote.implied_volatility
    if iv is None or not iv.is_finite() or not (thresholds.iv_min < iv < thresholds.iv_max):
        return Discard(quote.symbol, DiscardReason.MISSING_IV, f"iv={iv}")

    delta = quote.delta
    if delta is None or not delta.is_finite() or abs(delta) > ONE:
        return Discard(quote.symbol, DiscardReason.MISSING_GREEKS, f"delta={delta}")

    if quote.vega is None or not quote.vega.is_finite():
        return Discard(quote.symbol, DiscardReason.MISSING_GREEKS, f"vega={quote.vega}")

    return None


def short_delta_in_band(delta: Decimal, thresholds: ActuaryThresholds) -> bool:
    """The SHORT_DELTA_RANGE band, inclusive at both ends (§15.3)."""
    return thresholds.short_delta_min <= abs(delta) <= thresholds.short_delta_max

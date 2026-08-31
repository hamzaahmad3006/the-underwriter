"""Decimal arithmetic helpers.

NFR-013: all money is `Decimal`, never float. Option prices carry 2dp,
per-share values 4dp, ratios 6dp.

Rounding is deliberately asymmetric where it touches risk: a loss rounds *up*
and a credit rounds *down*, so quantisation can never make a position look
cheaper than it is. With 2dp inputs both are no-ops; they exist for the case
where they are not.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
PER_SHARE = Decimal("0.0001")
RATIO = Decimal("0.000001")

ZERO = Decimal("0")
ONE = Decimal("1")
CONTRACT_MULTIPLIER = Decimal("100")


class MoneyError(ValueError):
    """A value could not be interpreted as finite decimal money."""


def to_decimal(value: object, *, field: str = "value") -> Decimal:
    """Coerce to Decimal, refusing anything non-finite.

    Floats are accepted (Alpaca hands us floats) but converted via `str` so the
    binary representation never leaks into the arithmetic.
    """
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, float | str):
        try:
            candidate = Decimal(str(value))
        except Exception as exc:
            raise MoneyError(f"{field}={value!r} is not decimal-convertible") from exc
    else:
        raise MoneyError(f"{field}={value!r} is not decimal-convertible")

    if not candidate.is_finite():
        raise MoneyError(f"{field}={value!r} is not finite")
    return candidate


def q_price(value: Decimal) -> Decimal:
    """Quantise an option price to 2dp."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def q_credit(value: Decimal) -> Decimal:
    """Quantise money we expect to receive — rounds down (conservative)."""
    return value.quantize(CENTS, rounding=ROUND_FLOOR)


def q_loss(value: Decimal) -> Decimal:
    """Quantise money we may lose — rounds up (conservative)."""
    return value.quantize(CENTS, rounding=ROUND_CEILING)


def q_per_share(value: Decimal) -> Decimal:
    """Quantise a per-share value to 4dp."""
    return value.quantize(PER_SHARE, rounding=ROUND_HALF_UP)


def q_ratio(value: Decimal) -> Decimal:
    """Quantise a dimensionless ratio to 6dp."""
    return value.quantize(RATIO, rounding=ROUND_HALF_UP)


def safe_div(numerator: Decimal, denominator: Decimal, *, field: str = "ratio") -> Decimal:
    """Divide, refusing a zero denominator rather than returning infinity."""
    if denominator == ZERO:
        raise MoneyError(f"{field}: division by zero")
    return numerator / denominator

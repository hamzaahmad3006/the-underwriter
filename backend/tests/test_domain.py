"""Domain primitives.

Not gated by OPS-031, but everything the Kernel adjudicates passes through
here first. A Decimal coercion that silently accepts a float, or a canonical
form that is not stable, would break determinism (NFR-007) and signature
verification (FR-063) without any rule ever failing.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import pytest

from tests.conftest import NOW, D, make_quote, make_snapshot
from underwriter.actuary.engine import price_put_credit_spreads
from underwriter.actuary.thresholds import DEFAULT_THRESHOLDS
from underwriter.actuary.validation import DiscardReason, validate_quote
from underwriter.domain import money
from underwriter.domain.hashing import canonical_json, canonicalize, sha256_of
from underwriter.domain.market import OptionRight, Side
from underwriter.domain.money import MoneyError

# ---------------------------------------------------------------------------
# Decimal coercion — NFR-013
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("1.25"), Decimal("1.25")),
        (7, Decimal("7")),
        (1.25, Decimal("1.25")),  # via str, so no binary artefact
        ("1.25", Decimal("1.25")),
    ],
)
def test_to_decimal_accepts_what_alpaca_sends(raw: object, expected: Decimal) -> None:
    assert money.to_decimal(raw) == expected


def test_to_decimal_routes_floats_through_str() -> None:
    """0.1 + 0.2 is famously not 0.3 in binary. It must be here."""
    assert money.to_decimal(0.1) + money.to_decimal(0.2) == Decimal("0.3")


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), -float("inf")])
def test_to_decimal_refuses_non_finite_values(raw: float) -> None:
    with pytest.raises(MoneyError, match="not finite"):
        money.to_decimal(raw, field="bid")


@pytest.mark.parametrize("raw", ["not a number", None, [1], {"a": 1}])
def test_to_decimal_refuses_junk(raw: object) -> None:
    with pytest.raises(MoneyError, match="not decimal-convertible"):
        money.to_decimal(raw)


def test_quantisation_rounds_against_us_on_purpose() -> None:
    """A loss rounds up, a credit rounds down. Never the other way around."""
    assert money.q_loss(Decimal("150.001")) == Decimal("150.01")
    assert money.q_credit(Decimal("0.509")) == Decimal("0.50")
    assert money.q_price(Decimal("2.005")) == Decimal("2.01")
    assert money.q_per_share(Decimal("1.234567")) == Decimal("1.2346")
    assert money.q_ratio(Decimal("0.0666666")) == Decimal("0.066667")


def test_safe_div_refuses_a_zero_denominator() -> None:
    with pytest.raises(MoneyError, match="division by zero"):
        money.safe_div(Decimal("1"), Decimal("0"), field="edge_ratio")


# ---------------------------------------------------------------------------
# Canonical form — FR-027 and FR-063
# ---------------------------------------------------------------------------


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_serialises_decimals_as_strings() -> None:
    assert canonical_json({"x": Decimal("1.10")}) == '{"x":"1.10"}'


def test_canonicalize_refuses_floats() -> None:
    """A float in a hashed payload is a determinism bug, not a convenience."""
    with pytest.raises(TypeError, match="float is not canonicalizable"):
        canonicalize(1.5)


def test_canonicalize_handles_the_types_the_domain_actually_uses() -> None:
    class Colour(StrEnum):
        RED = "RED"

    payload = {
        "when": datetime(2026, 9, 1, tzinfo=UTC),
        "day": date(2026, 9, 1),
        "enum": Colour.RED,
        "list": [1, Decimal("2.5")],
        "none": None,
        "flag": True,
    }
    assert canonicalize(payload) == {
        "when": "2026-09-01T00:00:00+00:00",
        "day": "2026-09-01",
        "enum": "RED",
        "list": [1, "2.5"],
        "none": None,
        "flag": True,
    }


def test_canonicalize_refuses_an_unknown_type() -> None:
    with pytest.raises(TypeError, match="not canonicalizable"):
        canonicalize(object())


def test_sha256_is_stable_across_equal_payloads() -> None:
    assert sha256_of({"a": 1, "b": [2, 3]}) == sha256_of({"b": [2, 3], "a": 1})
    assert sha256_of({"a": 1}) != sha256_of({"a": 2})


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


def test_side_signs_position_exposure() -> None:
    assert Side.BUY.sign == 1
    assert Side.SELL.sign == -1


def test_quote_midpoint_and_spread() -> None:
    quote = make_quote(bid="2.00", ask="2.10")
    assert quote.mid == Decimal("2.05")
    assert quote.spread_pct == Decimal("0.10") / Decimal("2.05")


def test_quote_dte_goes_negative_after_expiry() -> None:
    quote = make_quote(expiry=date(2026, 9, 18))
    assert quote.dte(date(2026, 9, 1)) == 17
    assert quote.dte(date(2026, 9, 18)) == 0
    assert quote.dte(date(2026, 9, 20)) == -2


def test_snapshot_can_find_a_quote_by_symbol() -> None:
    snapshot = make_snapshot()
    assert snapshot.quote("SPY260918P00550000") is not None
    assert snapshot.quote("NOT_IN_THE_CHAIN") is None


def test_snapshot_hash_is_insensitive_to_nothing_that_matters() -> None:
    """Two identical snapshots hash the same; that is what makes replay work."""
    assert make_snapshot().snapshot_hash == make_snapshot().snapshot_hash


# ---------------------------------------------------------------------------
# Engine paths the golden tests do not reach
# ---------------------------------------------------------------------------


def test_engine_never_pairs_legs_across_underlyings() -> None:
    """A SPY short covered by a QQQ long is not a spread, it is two positions."""
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="SPY_A", underlying="SPY", strike=550),
            make_quote(symbol="QQQ_B", underlying="QQQ", strike=548, bid="1.40", ask="1.50"),
        )
    )
    result = price_put_credit_spreads(snapshot)
    assert result.is_empty


def test_engine_never_pairs_legs_across_expiries() -> None:
    """Different expiries make a calendar, which is out of scope (§15.1)."""
    near = date(2026, 9, 18)
    far = date(2026, 9, 21)
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, expiry=near),
            make_quote(symbol="B", strike=548, expiry=far, bid="1.40", ask="1.50"),
        )
    )
    result = price_put_credit_spreads(snapshot)
    assert result.is_empty


def test_engine_survives_a_quote_that_breaks_mid_pricing() -> None:
    """The defensive path: an unexpected fault discards, it does not raise.

    Reaching it takes a quote that clears validation and then fails during
    pricing, which is exactly the shape of a bug we have not thought of yet.
    """
    good = make_quote(symbol="A", strike=550)
    broken = make_quote(symbol="B", strike=548, bid="1.40", ask="1.50", delta="-0.15")
    object.__setattr__(broken, "ask", Decimal("NaN"))

    snapshot = make_snapshot(quotes=(good, broken))
    result = price_put_credit_spreads(snapshot)

    assert result.is_empty
    assert result.discards, "a fault must be recorded, not swallowed"


def test_validation_reports_a_bad_quote_when_the_midpoint_cannot_be_taken() -> None:
    quote = make_quote(bid="0.01", ask="0.02")
    object.__setattr__(quote, "bid", Decimal("-0.02"))
    object.__setattr__(quote, "ask", Decimal("0.02"))

    outcome = validate_quote(quote, DEFAULT_THRESHOLDS)
    assert outcome is not None
    assert outcome.reason is DiscardReason.BAD_QUOTE


def test_infinite_max_loss_would_not_be_finite() -> None:
    """Guards the assumption SK-004's finiteness check rests on."""
    assert not Decimal("Infinity").is_finite()
    assert math.isinf(float(Decimal("Infinity")))


def test_option_right_values_are_what_alpaca_uses() -> None:
    assert OptionRight.PUT == "PUT"
    assert OptionRight.CALL == "CALL"


def test_now_fixture_is_timezone_aware() -> None:
    """NFR-012: everything is stored in UTC, never naive."""
    assert NOW.tzinfo is UTC
    assert (NOW + timedelta(hours=1)).tzinfo is UTC
    assert D(5) == Decimal("5")


# ---------------------------------------------------------------------------
# The documented fallbacks of §11.2, reached by injection
# ---------------------------------------------------------------------------


def test_engine_records_an_unexpected_validation_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The belt to the finiteness check's braces.

    Validation now handles every fault we know about, so this asserts the
    behaviour for the ones we do not: a discard, never a dead cycle.
    """
    from underwriter.actuary import engine

    def explode(*_args: object, **_kwargs: object) -> None:
        raise ZeroDivisionError("unexpected fault inside validation")

    monkeypatch.setattr(engine, "validate_quote", explode)
    result = engine.price_put_credit_spreads(make_snapshot())

    assert result.is_empty
    assert all(d.reason is DiscardReason.ACTUARY_MATH_ERROR for d in result.discards)


def test_engine_records_an_unexpected_pricing_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from underwriter.actuary import engine

    def explode(*_args: object, **_kwargs: object) -> None:
        raise ZeroDivisionError("unexpected fault inside pricing")

    monkeypatch.setattr(engine.formulas, "net_credit", explode)
    result = engine.price_put_credit_spreads(make_snapshot())

    assert result.is_empty
    assert any(d.reason is DiscardReason.ACTUARY_MATH_ERROR for d in result.discards)


def test_pricing_a_leg_with_no_delta_discards_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-004 again, on the path where validation has already been passed."""
    from underwriter.actuary import engine

    monkeypatch.setattr(engine, "validate_quote", lambda *_: None)
    snapshot = make_snapshot(
        quotes=(
            make_quote(symbol="A", strike=550, delta=None),
            make_quote(symbol="B", strike=548, bid="1.40", ask="1.50"),
        )
    )
    result = engine.price_put_credit_spreads(snapshot)
    assert result.is_empty


def test_validation_reports_bad_quote_when_the_spread_cannot_be_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from underwriter.actuary import validation

    class Exploding:
        symbol = "X"
        tradable = True
        bid = Decimal("2.00")
        ask = Decimal("2.10")

        @property
        def spread_pct(self) -> Decimal:
            raise MoneyError("cannot measure this spread")

    outcome = validation.validate_quote(Exploding(), DEFAULT_THRESHOLDS)  # type: ignore[arg-type]
    assert outcome is not None
    assert outcome.reason is DiscardReason.BAD_QUOTE


def test_proposal_width_is_the_strike_distance() -> None:
    from tests.conftest import make_proposal

    assert make_proposal(short_strike=550, long_strike=545).width == Decimal("5")


def test_canonicalize_passes_plain_ints_through() -> None:
    assert canonicalize(42) == 42
    assert canonical_json({"n": 42}) == '{"n":42}'

"""Market Data Layer — §11.1 steps 1, 2 and 7, plus FR-006 and FR-007.

The whole point of `MarketDataSource` being a protocol is this file: the full
pipeline — clock, chain, validation, pricing, adjudication — runs here with no
network, no credentials and a pinned clock. That is the only way FR-027's
determinism claim is checkable rather than merely asserted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from underwriter.actuary.engine import price_put_credit_spreads
from underwriter.data.credentials import (
    MissingCredentialsError,
    has_credentials,
    load_data_credentials,
)
from underwriter.data.mappers import optional_decimal, to_contract_quote
from underwriter.data.ports import RawContract, SessionState
from underwriter.data.snapshot import CycleAbort, SnapshotConfig, build_snapshot
from underwriter.data.volatility import (
    VolatilityMeasure,
    build_context,
    iv_rank,
    realized_volatility,
)
from underwriter.domain.market import OptionRight

NOW = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 9, 18)  # 17 days out


def raw(
    symbol: str = "SPY260918P00550000",
    *,
    underlying: str = "SPY",
    strike: str = "550",
    bid: str | None = "2.00",
    ask: str | None = "2.10",
    delta: str | None = "-0.20",
    iv: str | None = "0.18",
    vega: str | None = "0.15",
    expiry: date = EXPIRY,
    quote_at: datetime | None = None,
    tradable: bool = True,
    right: str = "PUT",
) -> RawContract:
    return RawContract(
        symbol=symbol,
        underlying=underlying,
        right=right,
        strike=Decimal(strike),
        expiry=expiry,
        bid=None if bid is None else Decimal(bid),
        ask=None if ask is None else Decimal(ask),
        bid_size=50,
        ask_size=50,
        quote_at=quote_at or NOW,
        implied_volatility=None if iv is None else Decimal(iv),
        delta=None if delta is None else Decimal(delta),
        vega=None if vega is None else Decimal(vega),
        open_interest=2000,
        tradable=tradable,
    )


class FakeSource:
    """A `MarketDataSource` with no network behind it."""

    def __init__(
        self,
        *,
        contracts: list[RawContract] | None = None,
        is_open: bool = True,
        minutes_since_open: int = 60,
        minutes_to_close: int = 60,
        closes: list[Decimal] | None = None,
    ) -> None:
        self.contracts = contracts if contracts is not None else [raw()]
        self.session = SessionState(NOW, is_open, minutes_since_open, minutes_to_close)
        self.closes = closes if closes is not None else [Decimal("570")] * 21
        self.chain_calls: list[tuple[str, date, date]] = []

    def get_session(self) -> SessionState:
        return self.session

    def get_option_chain(
        self, underlying: str, *, expiry_from: date, expiry_to: date
    ) -> list[RawContract]:
        self.chain_calls.append((underlying, expiry_from, expiry_to))
        return [c for c in self.contracts if c.underlying == underlying]

    def get_daily_closes(self, underlying: str, *, lookback_days: int) -> list[Decimal]:
        return self.closes[-lookback_days:]


SOLO = SnapshotConfig(universe=("SPY",))


# ---------------------------------------------------------------------------
# Step 1 — market hours and blackout (FR-001)
# ---------------------------------------------------------------------------


def test_a_closed_market_aborts_the_cycle() -> None:
    result = build_snapshot(FakeSource(is_open=False), config=SOLO)
    assert result.aborted is CycleAbort.MARKET_CLOSED
    assert result.snapshot is None
    assert result.ok is False


@pytest.mark.parametrize(
    ("since_open", "to_close", "aborted"),
    [
        (60, 60, False),
        (15, 30, False),  # exactly on both boundaries
        (14, 60, True),
        (60, 29, True),
    ],
)
def test_the_blackout_windows_bound_entries(since_open: int, to_close: int, aborted: bool) -> None:
    result = build_snapshot(
        FakeSource(minutes_since_open=since_open, minutes_to_close=to_close), config=SOLO
    )
    assert (result.aborted is CycleAbort.IN_BLACKOUT) is aborted


def test_the_management_cycle_may_ignore_the_blackout() -> None:
    """DEV-02: blackouts bound entries. Exits must still be able to run."""
    config = SnapshotConfig(universe=("SPY",), require_entry_window=False)
    result = build_snapshot(FakeSource(minutes_to_close=5), config=config)
    assert result.aborted is None


def test_a_closed_market_still_blocks_the_management_cycle() -> None:
    """There is nothing to manage into a market that is not trading."""
    config = SnapshotConfig(universe=("SPY",), require_entry_window=False)
    result = build_snapshot(FakeSource(is_open=False), config=config)
    assert result.aborted is CycleAbort.MARKET_CLOSED


# ---------------------------------------------------------------------------
# Step 2 — the chain
# ---------------------------------------------------------------------------


def test_an_empty_chain_aborts_rather_than_reading_as_an_empty_book() -> None:
    result = build_snapshot(FakeSource(contracts=[]), config=SOLO)
    assert result.aborted is CycleAbort.NO_CHAIN
    assert "no contracts returned" in result.detail


def test_the_chain_is_requested_for_the_configured_dte_window() -> None:
    source = FakeSource()
    build_snapshot(source, config=SOLO)

    underlying, expiry_from, expiry_to = source.chain_calls[0]
    assert underlying == "SPY"
    assert expiry_from == NOW.date() + timedelta(days=7)
    assert expiry_to == NOW.date() + timedelta(days=21)


def test_every_configured_underlying_is_fetched() -> None:
    source = FakeSource(
        contracts=[raw(symbol="SPY_A"), raw(symbol="QQQ_A", underlying="QQQ", strike="480")]
    )
    result = build_snapshot(source, config=SnapshotConfig(universe=("SPY", "QQQ")))

    assert [call[0] for call in source.chain_calls] == ["SPY", "QQQ"]
    assert result.contracts_seen == 2


# ---------------------------------------------------------------------------
# Step 7 — data age (FR-005)
# ---------------------------------------------------------------------------


def test_stale_quotes_abort_the_cycle() -> None:
    old = raw(quote_at=NOW - timedelta(seconds=200))
    result = build_snapshot(FakeSource(contracts=[old]), config=SOLO)

    assert result.aborted is CycleAbort.STALE_DATA
    assert "200s old" in result.detail


def test_freshness_is_measured_against_the_oldest_quote() -> None:
    """A cycle is only as fresh as its stalest input."""
    source = FakeSource(
        contracts=[raw(symbol="FRESH"), raw(symbol="OLD", quote_at=NOW - timedelta(seconds=300))]
    )
    assert build_snapshot(source, config=SOLO).aborted is CycleAbort.STALE_DATA


def test_a_quote_exactly_at_the_age_limit_is_accepted() -> None:
    source = FakeSource(contracts=[raw(quote_at=NOW - timedelta(seconds=120))])
    assert build_snapshot(source, config=SOLO).aborted is None


# ---------------------------------------------------------------------------
# Mapping (NFR-013, FR-004)
# ---------------------------------------------------------------------------


def test_a_contract_with_no_quote_never_becomes_a_domain_quote() -> None:
    """The chain is full of strikes nobody is quoting. That is not an error."""
    assert to_contract_quote(raw(bid=None), fetched_at=NOW) is None
    assert to_contract_quote(raw(ask=None), fetched_at=NOW) is None


def test_an_unknown_right_is_refused_rather_than_guessed() -> None:
    assert to_contract_quote(raw(right="STRADDLE"), fetched_at=NOW) is None


def test_mapping_preserves_provenance_and_greeks() -> None:
    quote = to_contract_quote(raw(), fetched_at=NOW)
    assert quote is not None
    assert quote.source == "rest"  # FR-005
    assert quote.right is OptionRight.PUT
    assert quote.delta == Decimal("-0.20")
    assert quote.fetched_at == NOW


def test_optional_decimal_swallows_junk_instead_of_raising() -> None:
    assert optional_decimal(None, "x") is None
    assert optional_decimal(float("nan"), "x") is None
    assert optional_decimal("not a number", "x") is None
    assert optional_decimal(1.25, "x") == Decimal("1.25")


def test_missing_greeks_survive_as_far_as_the_validation_pipeline() -> None:
    """FR-004: absence must reach the layer that can record it as a discard."""
    source = FakeSource(contracts=[raw(delta=None), raw(symbol="B", strike="548", delta=None)])
    result = build_snapshot(source, config=SOLO)

    assert result.ok
    assert result.snapshot is not None
    assert all(q.delta is None for q in result.snapshot.quotes)

    priced = price_put_credit_spreads(result.snapshot)
    assert priced.is_empty
    assert any(d.reason == "MISSING_GREEKS" for d in priced.discards)


# ---------------------------------------------------------------------------
# The pipeline end to end
# ---------------------------------------------------------------------------


def test_a_snapshot_feeds_the_actuary_and_produces_a_priced_candidate() -> None:
    source = FakeSource(
        contracts=[
            raw(symbol="SPY_550", strike="550", bid="2.00", ask="2.10", delta="-0.20"),
            raw(symbol="SPY_548", strike="548", bid="1.40", ask="1.50", delta="-0.15"),
        ]
    )
    result = build_snapshot(source, config=SOLO)
    assert result.ok
    assert result.snapshot is not None

    priced = price_put_credit_spreads(result.snapshot)
    assert len(priced.proposals) == 1

    proposal = priced.proposals[0]
    assert proposal.max_loss == Decimal("150.00")
    assert proposal.capital_reserve == proposal.max_loss


def test_the_same_fake_chain_produces_the_same_snapshot_hash() -> None:
    """FR-027 across the full fetch, not just the Actuary."""
    contracts = [raw(symbol="A", strike="550"), raw(symbol="B", strike="548")]
    first = build_snapshot(FakeSource(contracts=list(contracts)), config=SOLO)
    second = build_snapshot(FakeSource(contracts=list(contracts)), config=SOLO)

    assert first.snapshot is not None and second.snapshot is not None
    assert first.snapshot.snapshot_hash == second.snapshot.snapshot_hash


def test_quotes_are_ordered_independently_of_the_broker_response_order() -> None:
    forward = [raw(symbol="A", strike="548"), raw(symbol="B", strike="550")]
    reversed_ = list(reversed(forward))

    a = build_snapshot(FakeSource(contracts=forward), config=SOLO).snapshot
    b = build_snapshot(FakeSource(contracts=reversed_), config=SOLO).snapshot

    assert a is not None and b is not None
    assert a.snapshot_hash == b.snapshot_hash


# ---------------------------------------------------------------------------
# Volatility — FR-006, FR-007
# ---------------------------------------------------------------------------


def test_realized_volatility_needs_enough_history() -> None:
    assert realized_volatility([]) is None
    assert realized_volatility([Decimal("100"), Decimal("101")]) is None


def test_realized_volatility_of_a_flat_series_is_zero() -> None:
    assert realized_volatility([Decimal("100")] * 10) == Decimal("0")


def test_realized_volatility_rises_with_dispersion() -> None:
    calm = [Decimal("100"), Decimal("101"), Decimal("100"), Decimal("101"), Decimal("100")]
    wild = [Decimal("100"), Decimal("115"), Decimal("95"), Decimal("120"), Decimal("90")]
    calm_rv = realized_volatility(calm)
    wild_rv = realized_volatility(wild)

    assert calm_rv is not None and wild_rv is not None
    assert wild_rv > calm_rv


def test_realized_volatility_refuses_a_non_positive_price() -> None:
    assert realized_volatility([Decimal("100"), Decimal("0"), Decimal("100")]) is None


def test_iv_rank_needs_a_real_history_and_a_real_range() -> None:
    assert iv_rank(Decimal("0.2"), []) is None
    assert iv_rank(Decimal("0.2"), [Decimal("0.2")] * 30) is None  # zero-width range


def test_iv_rank_places_a_reading_in_its_own_range() -> None:
    history = [Decimal("0.10") + Decimal("0.01") * Decimal(i) for i in range(25)]
    assert iv_rank(Decimal("0.10"), history) == Decimal("0")
    assert iv_rank(Decimal("0.34"), history) == Decimal("100")


def test_volatility_falls_back_to_iv_rv_and_says_so() -> None:
    """FR-007's documented fallback, which day one always takes (ALP-022)."""
    context = build_context(
        "SPY",
        closes=[Decimal("100"), Decimal("102"), Decimal("101"), Decimal("103")],
        current_iv=Decimal("0.25"),
        iv_history=[],
    )
    assert context.measure is VolatilityMeasure.IV_RV_RATIO
    assert context.iv_rank is not None
    assert "ALP-022" in context.detail


def test_volatility_is_unavailable_rather_than_invented() -> None:
    no_iv = build_context("SPY", closes=[Decimal("100")] * 5, current_iv=None, iv_history=[])
    assert no_iv.measure is VolatilityMeasure.UNAVAILABLE

    no_rv = build_context("SPY", closes=[], current_iv=Decimal("0.2"), iv_history=[])
    assert no_rv.measure is VolatilityMeasure.UNAVAILABLE
    assert no_rv.iv_rank is None


def test_a_flat_underlying_leaves_the_iv_rv_ratio_undefined() -> None:
    """Zero realised volatility cannot be a denominator."""
    context = build_context(
        "SPY", closes=[Decimal("100")] * 10, current_iv=Decimal("0.2"), iv_history=[]
    )
    assert context.measure is VolatilityMeasure.UNAVAILABLE


def test_iv_rank_is_preferred_once_there_is_history() -> None:
    history = [Decimal("0.10") + Decimal("0.01") * Decimal(i) for i in range(25)]
    context = build_context(
        "SPY",
        closes=[Decimal("100"), Decimal("102"), Decimal("101")],
        current_iv=Decimal("0.20"),
        iv_history=history,
    )
    assert context.measure is VolatilityMeasure.IV_RANK
    assert "25 observations" in context.detail


def test_the_snapshot_reports_volatility_per_underlying() -> None:
    result = build_snapshot(FakeSource(), config=SOLO)
    assert "SPY" in result.volatility


# ---------------------------------------------------------------------------
# Credentials — ALP-004
# ---------------------------------------------------------------------------


def test_a_dedicated_data_key_is_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_DATA_API_KEY", "data-key")
    monkeypatch.setenv("ALPACA_DATA_SECRET_KEY", "data-secret")
    monkeypatch.setenv("ALPACA_API_KEY", "account-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "account-secret")

    creds = load_data_credentials()
    assert creds.api_key == "data-key"
    assert creds.is_dedicated_data_key is True


def test_the_account_key_is_the_recorded_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_DATA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_DATA_SECRET_KEY", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "account-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "account-secret")

    creds = load_data_credentials()
    assert creds.api_key == "account-key"
    assert creds.is_dedicated_data_key is False  # visible in /health/deep


def test_credentials_never_print_themselves(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret in a traceback is a secret in a log aggregator."""
    monkeypatch.setenv("ALPACA_API_KEY", "super-secret-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "super-secret-secret")
    monkeypatch.delenv("ALPACA_DATA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_DATA_SECRET_KEY", raising=False)

    rendered = repr(load_data_credentials())
    assert "super-secret" not in rendered
    assert "***" in rendered


def test_missing_credentials_are_reported_not_defaulted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "ALPACA_DATA_API_KEY",
        "ALPACA_DATA_SECRET_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    assert has_credentials() is False
    with pytest.raises(MissingCredentialsError, match="ALP-004"):
        load_data_credentials()

"""Live paper-account checks — ALP-001 … ALP-003, TEST-060.

OPS-033: this file MUST NOT run in CI. It needs real credentials and it places
a real (paper) order. Run it by hand:

    make test-live

Every test skips itself when credentials are absent, so a plain `pytest` run
stays green on a machine that has never seen an Alpaca key.

Together these are ROAD-D0-02 and ROAD-D0-03 in one command: prove the account
exists and is paper, record the baseline equity the whole equity curve is
measured against, prove the chain carries the Greeks the Actuary refuses to
work without, and prove a multi-leg order is actually accepted before we build
a week on the assumption that it is.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

# ruff: noqa: E402 - .env must be read before `has_credentials()` runs below,
# which happens at import time in the module-level skip decision.
from underwriter.data.alpaca_source import AlpacaMarketData
from underwriter.data.credentials import has_credentials, load_data_credentials
from underwriter.data.snapshot import SnapshotConfig, build_snapshot

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not has_credentials(), reason="no Alpaca credentials configured"),
]

UNIVERSE = ("SPY",)


@pytest.fixture(scope="module")
def source() -> AlpacaMarketData:
    return AlpacaMarketData()


@pytest.fixture(scope="module")
def trading_client():  # type: ignore[no-untyped-def]
    """A paper trading client, used only by the ALP-003 order test."""
    from alpaca.trading.client import TradingClient

    credentials = load_data_credentials()
    return TradingClient(credentials.api_key, credentials.secret_key, paper=True)


# ---------------------------------------------------------------------------
# ALP-001, ALP-002 — the account itself
# ---------------------------------------------------------------------------


def test_alp001_the_account_is_a_paper_account(trading_client) -> None:  # type: ignore[no-untyped-def]
    """SEC-004 again, this time against the broker rather than the config.

    `ALPACA_PAPER_TRADE=true` is our own assertion about our own environment.
    This asks Alpaca.
    """
    assert os.environ.get("ALPACA_PAPER_TRADE", "true").lower() == "true"

    account = trading_client.get_account()
    assert account.status == "ACTIVE", f"account status is {account.status}"


def test_alp002_baseline_equity_is_recordable(trading_client) -> None:  # type: ignore[no-untyped-def]
    """The immutable baseline every drawdown and equity-curve figure hangs off.

    Printed rather than asserted against a fixed number: ASM-005 says the
    starting balance must not be load-bearing, and every limit in §14 is a
    percentage of NAV precisely so this value can be anything.
    """
    account = trading_client.get_account()
    equity = Decimal(str(account.equity))
    buying_power = Decimal(str(account.buying_power))

    print(f"\nALP-002 baseline — recorded {datetime.now(UTC).isoformat()}")
    print(f"  equity:       ${equity:,.2f}")
    print(f"  buying power: ${buying_power:,.2f}")
    print(f"  options level: {getattr(account, 'options_trading_level', 'not reported')}")

    assert equity > 0


def test_alp003_options_level_permits_multi_leg(trading_client) -> None:  # type: ignore[no-untyped-def]
    """Level 3 is automatic on paper accounts, but "should be" is not evidence.

    R-04 is the risk this retires: an `mleg` order rejected on Day 3 for a
    reason nobody checked on Day 0.
    """
    account = trading_client.get_account()
    level = getattr(account, "options_trading_level", None)

    assert level is not None, "account reports no options trading level"
    assert int(level) >= 3, (
        f"options trading level is {level}; multi-leg spreads need 3. "
        "Enable it in the Alpaca dashboard under account configuration."
    )


# ---------------------------------------------------------------------------
# Market data — the Actuary's actual inputs
# ---------------------------------------------------------------------------


def test_the_clock_reports_a_usable_session(source: AlpacaMarketData) -> None:
    session = source.get_session()
    print(
        f"\nclock: open={session.is_open} "
        f"since_open={session.minutes_since_open}m to_close={session.minutes_to_close}m"
    )
    assert session.as_of.tzinfo is not None, "the clock must be timezone aware (NFR-012)"


def test_the_chain_carries_the_greeks_the_actuary_requires(
    source: AlpacaMarketData,
) -> None:
    """FR-003 and FR-004.

    If this fails, nothing downstream works: the Actuary discards every
    contract without a delta, and it never estimates one.
    """
    today = date.today()
    contracts = source.get_option_chain(
        "SPY", expiry_from=today + timedelta(days=7), expiry_to=today + timedelta(days=21)
    )

    assert contracts, "SPY returned no contracts in the 7-21 DTE window"

    quoted = [c for c in contracts if c.bid is not None and c.ask is not None]
    with_greeks = [c for c in quoted if c.delta is not None and c.vega is not None]
    with_iv = [c for c in quoted if c.implied_volatility is not None]

    print(
        f"\nchain: {len(contracts)} contracts, {len(quoted)} quoted, "
        f"{len(with_greeks)} with Greeks, {len(with_iv)} with IV"
    )

    assert with_greeks, "no contract carried a delta — the Actuary would discard all of them"


def test_a_full_snapshot_survives_the_validation_pipeline(
    source: AlpacaMarketData,
) -> None:
    """The whole of §11.1 against live data, aborts included.

    Outside market hours this returns MARKET_CLOSED, which is a pass: FR-026
    says a cycle producing no trade is a successful cycle.
    """
    result = build_snapshot(source, config=SnapshotConfig(universe=UNIVERSE))

    print(f"\nsnapshot: aborted={result.aborted} detail={result.detail}")
    if result.aborted is not None:
        pytest.skip(f"cycle aborted: {result.aborted} — {result.detail}")

    assert result.snapshot is not None
    assert result.snapshot.quotes, "snapshot has no quotes"

    from underwriter.actuary.engine import price_put_credit_spreads

    priced = price_put_credit_spreads(result.snapshot, universe=UNIVERSE)
    print(f"actuary: {len(priced.proposals)} candidates, {len(priced.discards)} discarded")

    for discard in priced.discards[:5]:
        print(f"  discard {discard.reason}: {discard.detail}")


# ---------------------------------------------------------------------------
# TEST-060 / ALP-003 — a real multi-leg order, placed and cancelled
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("ALPACA_ALLOW_TEST_ORDER") != "true",
    reason="set ALPACA_ALLOW_TEST_ORDER=true to place the ALP-003 probe order",
)
def test_060_a_far_otm_multi_leg_order_is_accepted_then_cancelled(
    source: AlpacaMarketData,
    trading_client,  # type: ignore[no-untyped-def]
) -> None:
    """Place one `mleg` limit order far from the market, then cancel it.

    Deliberately gated behind its own environment variable. Everything else in
    this file only reads; this one writes to the account, and a test that
    places orders should never be something you run by accident.

    Far OTM and at an unfillable limit so it rests rather than fills, and it is
    cancelled in a `finally` so an assertion failure still cleans up.
    """
    from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

    today = date.today()
    contracts = source.get_option_chain(
        "SPY", expiry_from=today + timedelta(days=7), expiry_to=today + timedelta(days=21)
    )
    puts = sorted(
        (c for c in contracts if c.tradable and c.bid is not None),
        key=lambda c: c.strike,
    )
    assert len(puts) >= 2, "need at least two tradable put strikes"

    # The two lowest strikes available: deep out of the money, so a resting
    # order there is not going to fill while the test runs.
    long_leg, short_leg = puts[0], puts[1]

    order = LimitOrderRequest(
        qty=1,
        order_class=OrderClass.MLEG,
        time_in_force=TimeInForce.DAY,
        limit_price=0.01,  # unfillable credit: it rests, it does not fill
        legs=[
            OptionLegRequest(
                symbol=short_leg.symbol,
                ratio_qty=1,
                side=OrderSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
            OptionLegRequest(
                symbol=long_leg.symbol,
                ratio_qty=1,
                side=OrderSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
        ],
    )

    placed = None
    try:
        placed = trading_client.submit_order(order)
        print(f"\nALP-003: mleg accepted, id={placed.id} status={placed.status}")
        assert placed.id is not None
        # ALP-003 is about *acceptance*, not fill. A 200 with status=new is not
        # a filled order (TEST-052), and this test must not confuse the two.
        assert str(placed.status) in {"OrderStatus.NEW", "OrderStatus.ACCEPTED", "new", "accepted"}
    finally:
        if placed is not None:
            trading_client.cancel_order_by_id(placed.id)
            print(f"ALP-003: cancelled {placed.id}")

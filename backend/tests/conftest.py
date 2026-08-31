"""Builders for Kernel and Actuary tests.

Every builder produces a *healthy* value by default, so a test names only the
one thing it is about. That keeps a rule test to two lines and makes it obvious
which input the rule is actually reacting to.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from underwriter.domain.market import ContractQuote, MarketSnapshot, OptionRight, Side
from underwriter.domain.proposal import (
    Action,
    SpreadLeg,
    Structure,
    UnderwritingProposal,
)
from underwriter.kernel.context import AccountState, KernelContext, OpenPolicy, SystemMode

SECRET = "test-signing-secret-not-used-anywhere-real-0123456789"
NOW = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 9, 18)  # 17 days out from NOW


def D(value: str | int) -> Decimal:
    return Decimal(str(value))


def make_quote(
    *,
    symbol: str = "SPY260918P00550000",
    underlying: str = "SPY",
    right: OptionRight = OptionRight.PUT,
    strike: str | int = 550,
    expiry: date = EXPIRY,
    bid: str = "2.00",
    ask: str = "2.10",
    bid_size: int = 50,
    ask_size: int = 50,
    open_interest: int | None = 2000,
    implied_volatility: str | None = "0.18",
    delta: str | None = "-0.20",
    vega: str | None = "0.15",
    tradable: bool = True,
    fetched_at: datetime = NOW,
) -> ContractQuote:
    return ContractQuote(
        symbol=symbol,
        underlying=underlying,
        right=right,
        strike=D(strike),
        expiry=expiry,
        bid=D(bid),
        ask=D(ask),
        bid_size=bid_size,
        ask_size=ask_size,
        fetched_at=fetched_at,
        tradable=tradable,
        open_interest=open_interest,
        implied_volatility=None if implied_volatility is None else D(implied_volatility),
        delta=None if delta is None else D(delta),
        vega=None if vega is None else D(vega),
    )


def make_snapshot(
    quotes: tuple[ContractQuote, ...] | None = None,
    *,
    as_of: datetime = NOW,
) -> MarketSnapshot:
    if quotes is None:
        quotes = (
            make_quote(symbol="SPY260918P00550000", strike=550, bid="2.00", ask="2.10"),
            make_quote(
                symbol="SPY260918P00548000",
                strike=548,
                bid="1.40",
                ask="1.50",
                delta="-0.15",
            ),
        )
    return MarketSnapshot(as_of=as_of, underlying_prices={"SPY": D(570)}, quotes=quotes)


def make_proposal(
    *,
    candidate_id: str = "cand_test000000001",
    underlying: str = "SPY",
    action: Action = Action.OPEN,
    short_strike: str | int = 550,
    long_strike: str | int = 548,
    expiry: date = EXPIRY,
    dte: int = 17,
    net_credit: str = "0.50",
    max_profit: str = "50.00",
    max_loss: str = "150.00",
    capital_reserve: str | None = None,
    edge_ratio: str = "0.10",
    liquidity_score: str = "0.80",
    max_leg_spread_pct: str = "0.05",
    short_delta: str = "-0.20",
    net_delta: str = "5.00",
    net_vega: str = "-7.00",
    greeks_complete: bool = True,
    legs: tuple[SpreadLeg, ...] | None = None,
) -> UnderwritingProposal:
    """A proposal that passes every rule unless a test changes one field."""
    if legs is None:
        legs = (
            SpreadLeg(
                symbol=f"{underlying}260918P{int(D(short_strike)):05d}000",
                right=OptionRight.PUT,
                side=Side.SELL,
                strike=D(short_strike),
                expiry=expiry,
            ),
            SpreadLeg(
                symbol=f"{underlying}260918P{int(D(long_strike)):05d}000",
                right=OptionRight.PUT,
                side=Side.BUY,
                strike=D(long_strike),
                expiry=expiry,
            ),
        )
    return UnderwritingProposal(
        candidate_id=candidate_id,
        underlying=underlying,
        structure=Structure.PUT_CREDIT_SPREAD,
        action=action,
        legs=legs,
        short_strike=D(short_strike),
        long_strike=D(long_strike),
        expiry=expiry,
        dte=dte,
        net_credit=D(net_credit),
        max_profit=D(max_profit),
        max_loss=D(max_loss),
        capital_reserve=D(max_loss if capital_reserve is None else capital_reserve),
        breakeven=D(short_strike) - D(net_credit),
        credit_to_width=D("0.25"),
        p_loss_proxy=D("0.20"),
        p_profit_proxy=D("0.80"),
        expected_value=D("10.00"),
        edge_ratio=D(edge_ratio),
        liquidity_score=D(liquidity_score),
        max_leg_spread_pct=D(max_leg_spread_pct),
        short_delta=D(short_delta),
        net_delta=D(net_delta),
        net_vega=D(net_vega),
        greeks_complete=greeks_complete,
        snapshot_hash="a" * 64,
        snapshot_as_of=NOW,
    )


def make_account(
    *,
    nav: str = "100000.00",
    equity: str | None = None,
    buying_power: str = "200000.00",
    peak_equity: str | None = None,
    daily_realized_pnl: str = "0.00",
    read_ok: bool = True,
) -> AccountState:
    return AccountState(
        nav=D(nav),
        equity=D(nav if equity is None else equity),
        buying_power=D(buying_power),
        peak_equity=D(nav if peak_equity is None else peak_equity),
        daily_realized_pnl=D(daily_realized_pnl),
        as_of=NOW,
        read_ok=read_ok,
    )


def make_policy(
    *,
    policy_id: str = "pol_0001",
    underlying: str = "SPY",
    short_strike: str | int = 540,
    long_strike: str | int = 538,
    expiry: date = EXPIRY,
    contracts: int = 1,
    max_loss: str = "150.00",
    reserve: str | None = None,
) -> OpenPolicy:
    return OpenPolicy(
        policy_id=policy_id,
        underlying=underlying,
        structure=Structure.PUT_CREDIT_SPREAD,
        short_strike=D(short_strike),
        long_strike=D(long_strike),
        expiry=expiry,
        contracts=contracts,
        max_loss=D(max_loss),
        reserve=D(max_loss if reserve is None else reserve),
    )


def make_context(
    *,
    now: datetime = NOW,
    account: AccountState | None = None,
    data_age_sec: int = 5,
    mode: SystemMode = SystemMode.ACTIVE,
    kill_switch_engaged: bool = False,
    market_open: bool = True,
    minutes_since_open: int = 60,
    minutes_to_close: int = 60,
    open_policies: tuple[OpenPolicy, ...] = (),
    portfolio_net_delta: str = "0",
    portfolio_net_vega: str = "0",
    supplied_candidate_ids: frozenset[str] | None = None,
    breached_underlyings: frozenset[str] = frozenset(),
    existing_client_order_ids: frozenset[str] = frozenset(),
) -> KernelContext:
    return KernelContext(
        now=now,
        account=account or make_account(),
        data_as_of=now - timedelta(seconds=data_age_sec),
        mode=mode,
        kill_switch_engaged=kill_switch_engaged,
        market_open=market_open,
        minutes_since_open=minutes_since_open,
        minutes_to_close=minutes_to_close,
        open_policies=open_policies,
        portfolio_net_delta=D(portfolio_net_delta),
        portfolio_net_vega=D(portfolio_net_vega),
        supplied_candidate_ids=(
            frozenset({"cand_test000000001"})
            if supplied_candidate_ids is None
            else supplied_candidate_ids
        ),
        breached_underlyings=breached_underlyings,
        existing_client_order_ids=existing_client_order_ids,
    )


@pytest.fixture
def secret() -> str:
    return SECRET


@pytest.fixture
def healthy_proposal() -> UnderwritingProposal:
    return make_proposal()


@pytest.fixture
def healthy_context() -> KernelContext:
    return make_context()

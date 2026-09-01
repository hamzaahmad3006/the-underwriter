"""The validation pipeline — §11.1, steps 1 to 8, in order.

Per-contract checks (3 to 6, 8) live in `actuary/validation.py`, because the
Actuary needs the same answers and duplicating them would let the two drift.
This module owns the cycle-level ones, which abort the whole cycle rather than
discard one contract:

1. Market open and outside the blackout windows, else abort.
2. Chain retrieved and non-empty, else `NO_CHAIN`.
7. Every input younger than `MAX_DATA_AGE_SEC`, else `STALE_DATA`.

FR-026 is the attitude to hold throughout: a cycle that produces no trade is a
successful cycle. An abort here is a recorded outcome, never an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from underwriter.data.mappers import to_contract_quote
from underwriter.data.ports import MarketDataSource, RawContract, SessionState
from underwriter.data.volatility import VolatilityContext, build_context
from underwriter.domain.market import ContractQuote, MarketSnapshot
from underwriter.domain.money import ZERO


class CycleAbort(StrEnum):
    """Why a cycle produced nothing. Persisted as a `system_event` (FR-026)."""

    MARKET_CLOSED = "MARKET_CLOSED"
    IN_BLACKOUT = "IN_BLACKOUT"
    NO_CHAIN = "NO_CHAIN"
    STALE_DATA = "STALE_DATA"


@dataclass(frozen=True, slots=True)
class SnapshotConfig:
    """Everything the fetch needs, all of it from version-controlled config."""

    universe: tuple[str, ...] = ("SPY", "QQQ", "IWM")
    dte_min: int = 7
    dte_max: int = 21
    max_data_age_sec: int = 120
    blackout_open_min: int = 15
    blackout_close_min: int = 30
    rv_lookback_days: int = 20
    # Entries need an open session outside the bells. Exits do not (DEV-02),
    # so the management cycle sets this false and still gets a snapshot.
    require_entry_window: bool = True


DEFAULT_SNAPSHOT_CONFIG = SnapshotConfig()


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """One fetch attempt: what came back, and what it means."""

    session: SessionState
    snapshot: MarketSnapshot | None = None
    volatility: dict[str, VolatilityContext] = field(default_factory=dict)
    aborted: CycleAbort | None = None
    detail: str = ""
    contracts_seen: int = 0

    @property
    def ok(self) -> bool:
        return self.aborted is None and self.snapshot is not None


def _abort(session: SessionState, reason: CycleAbort, detail: str) -> SnapshotResult:
    return SnapshotResult(session=session, aborted=reason, detail=detail)


def build_snapshot(
    source: MarketDataSource,
    *,
    config: SnapshotConfig = DEFAULT_SNAPSHOT_CONFIG,
    now: datetime | None = None,
) -> SnapshotResult:
    """Run the pipeline and return a snapshot, or the reason there isn't one.

    `now` is a parameter rather than a `datetime.now()` call so a test can pin
    the clock. The Actuary downstream reads its time from the snapshot alone,
    which is what makes TEST-024's determinism claim checkable.
    """
    session = source.get_session()
    as_of = now or session.as_of

    # Step 1 — market hours and blackout windows (FR-001).
    if not session.is_open:
        return _abort(session, CycleAbort.MARKET_CLOSED, "the market is closed")

    if config.require_entry_window and session.inside_blackout(
        config.blackout_open_min, config.blackout_close_min
    ):
        return _abort(
            session,
            CycleAbort.IN_BLACKOUT,
            f"inside the blackout window: {session.minutes_since_open}m since open, "
            f"{session.minutes_to_close}m to close",
        )

    expiry_from = as_of.date() + timedelta(days=config.dte_min)
    expiry_to = as_of.date() + timedelta(days=config.dte_max)

    raw: list[RawContract] = []
    for underlying in config.universe:
        raw.extend(
            source.get_option_chain(underlying, expiry_from=expiry_from, expiry_to=expiry_to)
        )

    # Step 2 — a chain that came back empty is an abort, not an empty book.
    if not raw:
        return _abort(
            session,
            CycleAbort.NO_CHAIN,
            f"no contracts returned for {', '.join(config.universe)} "
            f"expiring {expiry_from} to {expiry_to}",
        )

    quotes: list[ContractQuote] = []
    for contract in raw:
        quote = to_contract_quote(contract, fetched_at=as_of)
        if quote is not None:
            quotes.append(quote)

    # Step 7 — data age. Checked against the *oldest* quote, because a cycle is
    # only as fresh as its stalest input, and SK-018 re-adjudicates it anyway.
    if quotes:
        oldest = min(quote.fetched_at for quote in quotes)
        age_sec = (as_of - oldest).total_seconds()
        if age_sec > config.max_data_age_sec:
            return _abort(
                session,
                CycleAbort.STALE_DATA,
                f"oldest quote is {age_sec:.0f}s old, limit {config.max_data_age_sec}s",
            )

    underlying_prices = _estimate_underlying_prices(source, config)
    volatility = _volatility_by_underlying(source, config, quotes)

    snapshot = MarketSnapshot(
        as_of=as_of,
        underlying_prices=underlying_prices,
        quotes=tuple(sorted(quotes, key=lambda q: (q.underlying, q.expiry, q.strike, q.symbol))),
    )

    return SnapshotResult(
        session=session,
        snapshot=snapshot,
        volatility=volatility,
        detail=f"{len(quotes)} quotable contracts from {len(raw)} returned",
        contracts_seen=len(raw),
    )


def _estimate_underlying_prices(
    source: MarketDataSource, config: SnapshotConfig
) -> dict[str, Decimal]:
    """Latest daily close per underlying.

    The close is a reference price for display and breach checks, never an
    input to pricing — every priced number comes from the option's own quote.
    """
    prices: dict[str, Decimal] = {}
    for underlying in config.universe:
        closes = source.get_daily_closes(underlying, lookback_days=config.rv_lookback_days)
        if closes:
            prices[underlying] = closes[-1]
    return prices


def _volatility_by_underlying(
    source: MarketDataSource, config: SnapshotConfig, quotes: list[ContractQuote]
) -> dict[str, VolatilityContext]:
    """FR-006 and FR-007, per underlying.

    Current IV is taken as the median across that underlying's quoted strikes,
    which is steadier than any single contract's reading and needs no surface
    model to justify.
    """
    contexts: dict[str, VolatilityContext] = {}

    for underlying in config.universe:
        ivs = sorted(
            quote.implied_volatility
            for quote in quotes
            if quote.underlying == underlying and quote.implied_volatility is not None
        )
        current_iv = ivs[len(ivs) // 2] if ivs else None
        closes = source.get_daily_closes(underlying, lookback_days=config.rv_lookback_days)

        contexts[underlying] = build_context(
            underlying,
            closes=closes,
            current_iv=current_iv,
            # ALP-022: Alpaca publishes no historical chain snapshots, so IV
            # history can only be built forward from our own. On day one there
            # is none, and FR-007's IV/RV fallback carries every underlying.
            iv_history=[],
        )

    return contexts


def expiry_window(as_of: date, config: SnapshotConfig) -> tuple[date, date]:
    """The DTE window as dates, exposed for the candidates endpoint."""
    return (
        as_of + timedelta(days=config.dte_min),
        as_of + timedelta(days=config.dte_max),
    )


def total_open_interest(snapshot: MarketSnapshot) -> int:
    """Small helper for the dashboard's liquidity tile."""
    return sum(q.open_interest or 0 for q in snapshot.quotes) or int(ZERO)

"""The one file that imports alpaca-py.

Everything else in the system depends on `MarketDataSource`, so this adapter is
the only place a broker's model shapes leak in — and the only place to change
when they move.

Both clients here are constructed from read-only credentials (ALP-004). The
`TradingClient` is needed for the clock and for contract metadata, which is why
it is built with `paper=True` and never handed an order.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from alpaca.data.enums import OptionsFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import ContractType
from alpaca.trading.models import Clock, OptionContractsResponse
from alpaca.trading.requests import GetOptionContractsRequest

from underwriter.data.credentials import DataCredentials, load_data_credentials
from underwriter.data.mappers import optional_decimal
from underwriter.data.ports import RawContract, SessionState

# ALP-020: free accounts get the derived `indicative` feed, with trades delayed
# roughly 15 minutes. `opra` needs a subscription. The system must not claim
# fill-quality superiority on this feed, and the README says so.
DEFAULT_FEED = OptionsFeed.INDICATIVE

# ALP-023: 200 rpm on the trading API. The contract-metadata call is paged, so
# the page size is capped to keep one cycle well inside the budget.
CONTRACT_PAGE_LIMIT = 500


class AlpacaMarketData:
    """`MarketDataSource` backed by Alpaca's REST API.

    TD-06: REST is authoritative. MCP is the agent's tool surface, but
    correctness-critical reads happen here, on a path with no extra hop and no
    tool-call layer between the number and the decision.
    """

    def __init__(
        self,
        credentials: DataCredentials | None = None,
        feed: OptionsFeed = DEFAULT_FEED,
    ) -> None:
        creds = credentials or load_data_credentials()
        self._feed = feed
        self._options = OptionHistoricalDataClient(creds.api_key, creds.secret_key)
        self._stocks = StockHistoricalDataClient(creds.api_key, creds.secret_key)
        # paper=True is belt and braces: this client only reads, and even if it
        # were misused it could not reach a live account.
        self._trading = TradingClient(creds.api_key, creds.secret_key, paper=True)

    # -- FR-001 ------------------------------------------------------------

    def get_session(self) -> SessionState:
        """The market clock, reduced to what SK-017 adjudicates."""
        clock = cast(Clock, self._trading.get_clock())
        now = clock.timestamp

        if clock.is_open:
            # `next_open` is in the past while a session is running, so the
            # current open is derived from the close rather than read directly.
            minutes_to_close = max(0, int((clock.next_close - now).total_seconds() // 60))
            session_open = clock.next_close - timedelta(hours=6, minutes=30)
            minutes_since_open = max(0, int((now - session_open).total_seconds() // 60))
        else:
            minutes_to_close = 0
            minutes_since_open = 0

        return SessionState(
            as_of=now,
            is_open=bool(clock.is_open),
            minutes_since_open=minutes_since_open,
            minutes_to_close=minutes_to_close,
        )

    # -- FR-002, FR-003, FR-010 -------------------------------------------

    def get_option_chain(
        self, underlying: str, *, expiry_from: date, expiry_to: date
    ) -> list[RawContract]:
        """Puts in the DTE window, with quotes, IV and Greeks.

        Two calls, joined on symbol: the chain carries quotes and Greeks but no
        open interest or tradability, and the contracts endpoint carries those
        but no quotes. A contract missing from either side still comes back —
        absence is a discard reason for the validation pipeline to record, not
        something to silently drop here.
        """
        chain = self._options.get_option_chain(
            OptionChainRequest(
                underlying_symbol=underlying,
                feed=self._feed,
                type=ContractType.PUT,
                expiration_date_gte=expiry_from,
                expiration_date_lte=expiry_to,
            )
        )

        metadata = self._contract_metadata(underlying, expiry_from, expiry_to)
        fetched_at = datetime.now(UTC)
        contracts: list[RawContract] = []

        for symbol, snapshot in chain.items():
            meta = metadata.get(symbol)
            if meta is None:
                # FR-010: a symbol we cannot confirm exists and is tradable is
                # not one we will build an order from.
                continue

            quote = snapshot.latest_quote
            greeks = snapshot.greeks

            contracts.append(
                RawContract(
                    symbol=symbol,
                    underlying=underlying,
                    right="PUT",
                    strike=meta["strike"],
                    expiry=meta["expiry"],
                    bid=optional_decimal(getattr(quote, "bid_price", None), "bid"),
                    ask=optional_decimal(getattr(quote, "ask_price", None), "ask"),
                    bid_size=int(getattr(quote, "bid_size", 0) or 0),
                    ask_size=int(getattr(quote, "ask_size", 0) or 0),
                    quote_at=getattr(quote, "timestamp", None) or fetched_at,
                    implied_volatility=optional_decimal(snapshot.implied_volatility, "iv"),
                    delta=optional_decimal(getattr(greeks, "delta", None), "delta"),
                    gamma=optional_decimal(getattr(greeks, "gamma", None), "gamma"),
                    theta=optional_decimal(getattr(greeks, "theta", None), "theta"),
                    vega=optional_decimal(getattr(greeks, "vega", None), "vega"),
                    rho=optional_decimal(getattr(greeks, "rho", None), "rho"),
                    open_interest=meta["open_interest"],
                    tradable=meta["tradable"],
                )
            )

        return contracts

    def _contract_metadata(
        self, underlying: str, expiry_from: date, expiry_to: date
    ) -> dict[str, dict[str, Any]]:
        """Strike, expiry, open interest and tradability, keyed by symbol."""
        metadata: dict[str, dict[str, Any]] = {}
        page_token: str | None = None

        while True:
            response = cast(
                OptionContractsResponse,
                self._trading.get_option_contracts(
                    GetOptionContractsRequest(
                        underlying_symbols=[underlying],
                        type=ContractType.PUT,
                        expiration_date_gte=expiry_from,
                        expiration_date_lte=expiry_to,
                        limit=CONTRACT_PAGE_LIMIT,
                        page_token=page_token,
                    )
                ),
            )

            for contract in response.option_contracts or []:
                strike = optional_decimal(contract.strike_price, "strike")
                if strike is None:
                    continue
                metadata[contract.symbol] = {
                    "strike": strike,
                    "expiry": contract.expiration_date,
                    "open_interest": (
                        int(contract.open_interest) if contract.open_interest is not None else None
                    ),
                    "tradable": bool(contract.tradable),
                }

            page_token = getattr(response, "next_page_token", None)
            if not page_token:
                return metadata

    # -- FR-006 ------------------------------------------------------------

    def get_daily_closes(self, underlying: str, *, lookback_days: int) -> list[Decimal]:
        """Daily closes, oldest first.

        The window is padded for weekends and holidays, then trimmed, so a
        20-trading-day request actually returns 20 trading days.
        """
        end = datetime.now(UTC)
        start = end - timedelta(days=lookback_days * 2 + 10)

        bars = self._stocks.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=underlying,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
            )
        )

        series = bars.data.get(underlying, []) if hasattr(bars, "data") else []
        closes: list[Decimal] = []
        for bar in series:
            close = optional_decimal(bar.close, "close")
            if close is not None:
                closes.append(close)

        return closes[-lookback_days:]

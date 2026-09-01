"""The only file in the system that holds trading credentials.

§10.2's authority boundary is this file's boundary. Everything above depends on
`BrokerPort`; only this class can transmit.

The error taxonomy matters as much as the calls. FR-087 retries transient
faults and never retries validation errors, so mapping a 422 to
`TransientBrokerError` would turn one clean rejection into three, and mapping a
429 to `PermanentBrokerError` would abandon a cycle over a moment's throttling.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, cast

from underwriter.execution.ports import (
    BrokerOrder,
    BrokerPosition,
    OrderState,
    PermanentBrokerError,
    TransientBrokerError,
)

TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class TradingCredentialsError(RuntimeError):
    """No trading credentials, so no orders. Nothing degrades to read-only here."""


class AlpacaBroker:
    """`BrokerPort` backed by Alpaca's paper trading API.

    `paper=True` is not configurable. SEC-004 makes paper-only a property of
    the system rather than of its configuration, and a constructor argument
    would be one refactor away from being passed `False`.
    """

    def __init__(self, *, api_key: str | None = None, secret_key: str | None = None) -> None:
        key = api_key or os.environ.get("ALPACA_API_KEY", "").strip()
        secret = secret_key or os.environ.get("ALPACA_SECRET_KEY", "").strip()

        if not key or not secret:
            raise TradingCredentialsError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY are required to transmit orders"
            )

        if os.environ.get("ALPACA_PAPER_TRADE", "true").strip().lower() != "true":
            raise TradingCredentialsError(
                "ALPACA_PAPER_TRADE must be 'true'. This system is paper-only (SEC-004)."
            )

        from alpaca.trading.client import TradingClient

        self._client = TradingClient(key, secret, paper=True)

    # -- mapping ---------------------------------------------------------

    @staticmethod
    def _classify(exc: Exception) -> Exception:
        """Transient or permanent. Getting this wrong costs either way."""
        status = getattr(exc, "status_code", None)
        if status is None:
            message = str(exc).lower()
            if any(word in message for word in ("timeout", "connection", "temporarily")):
                return TransientBrokerError(str(exc))
            return PermanentBrokerError(str(exc))

        if status in TRANSIENT_STATUSES:
            return TransientBrokerError(f"{status}: {exc}")
        return PermanentBrokerError(f"{status}: {exc}")

    @staticmethod
    def _to_order(raw: Any) -> BrokerOrder:
        def decimal_of(value: Any, default: str = "0") -> Decimal:
            return Decimal(str(value if value is not None else default))

        status = str(getattr(raw, "status", "new")).split(".")[-1].lower()
        try:
            state = OrderState(status)
        except ValueError:
            # An unknown status is not terminal. Assuming otherwise would end
            # the poll early and report a fill nobody confirmed (FR-084).
            state = OrderState.NEW

        return BrokerOrder(
            broker_order_id=str(getattr(raw, "id", "")),
            client_order_id=str(getattr(raw, "client_order_id", "")),
            state=state,
            filled_qty=decimal_of(getattr(raw, "filled_qty", 0)),
            ordered_qty=decimal_of(getattr(raw, "qty", 0)),
            filled_avg_price=(
                decimal_of(raw.filled_avg_price)
                if getattr(raw, "filled_avg_price", None) is not None
                else None
            ),
            submitted_at=getattr(raw, "submitted_at", None),
            raw={"status": status, "id": str(getattr(raw, "id", ""))},
        )

    # -- BrokerPort ------------------------------------------------------

    @staticmethod
    def _to_request(payload: dict[str, Any]) -> Any:
        """Translate the §17.3 payload into alpaca-py's typed request.

        The dict stays the canonical artifact — it is what §17.3 specifies and
        what `orders.request_json` audits — and this is only the transport
        shape. `limit_price` becomes a float here because that is what the SDK
        and the JSON wire accept; the Decimal remains the audited value, and
        nothing downstream computes from the float.
        """
        from alpaca.trading.enums import (
            OrderClass,
            OrderSide,
            PositionIntent,
            TimeInForce,
        )
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

        legs = [
            OptionLegRequest(
                symbol=leg["symbol"],
                side=OrderSide(leg["side"]),
                ratio_qty=int(leg["ratio_qty"]),
                position_intent=PositionIntent(leg["position_intent"]),
            )
            for leg in payload["legs"]
        ]

        return LimitOrderRequest(
            qty=int(payload["qty"]),
            limit_price=float(payload["limit_price"]),
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            client_order_id=payload["client_order_id"],
            legs=legs,
        )

    def submit_order(self, payload: dict[str, Any]) -> BrokerOrder:
        """Transmit an `mleg`. The payload is already fully validated."""
        try:
            return self._to_order(self._client.submit_order(order_data=self._to_request(payload)))
        except Exception as exc:
            raise self._classify(exc) from exc

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        try:
            raw = self._client.get_order_by_client_id(client_order_id)
        except Exception as exc:
            classified = self._classify(exc)
            if isinstance(classified, PermanentBrokerError) and "404" in str(exc):
                return None
            raise classified from exc

        return self._to_order(raw) if raw is not None else None

    def cancel_order(self, broker_order_id: str) -> None:
        try:
            self._client.cancel_order_by_id(broker_order_id)
        except Exception as exc:
            raise self._classify(exc) from exc

    def list_positions(self) -> list[BrokerPosition]:
        from alpaca.trading.models import Position

        try:
            positions = cast(list[Position], self._client.get_all_positions())
        except Exception as exc:
            raise self._classify(exc) from exc

        return [
            BrokerPosition(
                symbol=str(position.symbol),
                qty=Decimal(str(position.qty)),
                avg_entry_price=(
                    Decimal(str(position.avg_entry_price))
                    if getattr(position, "avg_entry_price", None) is not None
                    else None
                ),
                market_value=(
                    Decimal(str(position.market_value))
                    if getattr(position, "market_value", None) is not None
                    else None
                ),
                unrealized_pl=(
                    Decimal(str(position.unrealized_pl))
                    if getattr(position, "unrealized_pl", None) is not None
                    else None
                ),
            )
            for position in positions
        ]

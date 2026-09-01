"""What the engine needs from a broker.

A protocol again, for the same reason as the data layer: the guarded execution
path — signature check, retry policy, timeout, cancel, partial-fill detection —
has to be testable without placing real orders. A test that cannot exercise the
cancel path is a test that does not cover the case where money is at risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol


class OrderState(StrEnum):
    """Alpaca order states, reduced to what the engine branches on.

    `NEW` and `ACCEPTED` are explicitly *not* terminal. FR-084: an HTTP 200 is
    not a fill, and treating one as a fill is how a phantom policy is born.
    """

    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    PENDING_CANCEL = "pending_cancel"

    @property
    def is_terminal(self) -> bool:
        return self in {
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.REJECTED,
        }


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """One order as the broker currently sees it."""

    broker_order_id: str
    client_order_id: str
    state: OrderState
    filled_qty: Decimal
    ordered_qty: Decimal
    filled_avg_price: Decimal | None = None
    submitted_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_partial(self) -> bool:
        """Terminal but incomplete: some legs on, some not (F-13)."""
        return self.state.is_terminal and self.filled_qty > 0 and self.filled_qty < self.ordered_qty


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pl: Decimal | None = None


class TransientBrokerError(RuntimeError):
    """429, 5xx, or a network fault. Retryable with backoff (FR-087)."""


class PermanentBrokerError(RuntimeError):
    """A 4xx validation error. Retrying it just fails again more slowly."""


class BrokerPort(Protocol):
    """The trading surface. Only this package is allowed to hold one."""

    def submit_order(self, payload: dict[str, Any]) -> BrokerOrder: ...

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrder | None: ...

    def cancel_order(self, broker_order_id: str) -> None: ...

    def list_positions(self) -> list[BrokerPosition]: ...

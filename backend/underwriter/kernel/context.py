"""The authoritative state the Kernel adjudicates against.

SK-P5: account state is read from the Alpaca REST Trading API at decision time,
never from cache (FR-067). This module holds the *shape* of that read; fetching
it is the caller's job, which keeps the Kernel free of network code (FR-060)
and therefore trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from underwriter.domain.money import ZERO
from underwriter.domain.proposal import Structure


class SystemMode(StrEnum):
    """ERR-007 and SK-023. Boot is always MANAGE_ONLY until reconciled."""

    ACTIVE = "ACTIVE"
    MANAGE_ONLY = "MANAGE_ONLY"
    HALT = "HALT"


@dataclass(frozen=True, slots=True)
class OpenPolicy:
    """A live policy, as the book currently understands it."""

    policy_id: str
    underlying: str
    structure: Structure
    short_strike: Decimal
    long_strike: Decimal
    expiry: date
    contracts: int
    max_loss: Decimal  # remaining, for the whole position
    reserve: Decimal

    @property
    def assignment_cost(self) -> Decimal:
        """Cash needed to take delivery if every short leg were assigned.

        The long leg caps the *loss*, but not the transient cash requirement:
        you take delivery at the short strike first and exercise the long
        second. SK-012 is the rule that refuses to ignore that gap.
        """
        return self.short_strike * Decimal("100") * Decimal(self.contracts)


@dataclass(frozen=True, slots=True)
class AccountState:
    """One authoritative read of the Alpaca paper account."""

    nav: Decimal
    equity: Decimal
    buying_power: Decimal
    peak_equity: Decimal
    daily_realized_pnl: Decimal  # negative is a loss
    as_of: datetime
    read_ok: bool = True  # SK-025: false when the REST read failed

    @property
    def drawdown_pct(self) -> Decimal:
        """Fraction below peak equity. Zero when peak is unusable."""
        if self.peak_equity <= ZERO:
            return ZERO
        return (self.peak_equity - self.equity) / self.peak_equity


@dataclass(frozen=True, slots=True)
class KernelContext:
    """Everything outside the proposal that a verdict depends on."""

    now: datetime
    account: AccountState
    data_as_of: datetime  # oldest input timestamp in the cycle (SK-018)
    mode: SystemMode = SystemMode.ACTIVE
    kill_switch_engaged: bool = False

    market_open: bool = True
    minutes_since_open: int = 60
    minutes_to_close: int = 60

    open_policies: tuple[OpenPolicy, ...] = ()
    portfolio_net_delta: Decimal = ZERO
    portfolio_net_vega: Decimal = ZERO

    supplied_candidate_ids: frozenset[str] = frozenset()
    breached_underlyings: frozenset[str] = frozenset()
    existing_client_order_ids: frozenset[str] = frozenset()

    @property
    def total_reserve(self) -> Decimal:
        return sum((p.reserve for p in self.open_policies), ZERO)

    @property
    def portfolio_max_loss(self) -> Decimal:
        return sum((p.max_loss for p in self.open_policies), ZERO)

    @property
    def total_assignment_cost(self) -> Decimal:
        return sum((p.assignment_cost for p in self.open_policies), ZERO)

    def reserve_for(self, underlying: str) -> Decimal:
        return sum(
            (p.reserve for p in self.open_policies if p.underlying == underlying),
            ZERO,
        )

    @property
    def data_age_sec(self) -> Decimal:
        return Decimal(str((self.now - self.data_as_of).total_seconds()))

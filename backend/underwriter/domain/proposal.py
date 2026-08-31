"""The Actuary's output and the Kernel's input.

FR-025: a strictly typed proposal carrying every computed value plus a hash of
the snapshot it came from. The Kernel never recomputes these numbers — it
adjudicates them — so the proposal is the whole contract between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from underwriter.domain.hashing import sha256_of
from underwriter.domain.market import OptionRight, Side


class Structure(StrEnum):
    PUT_CREDIT_SPREAD = "PUT_CREDIT_SPREAD"


class Action(StrEnum):
    """SK-000 turns on this field: a close is risk-reducing and privileged."""

    OPEN = "OPEN"
    CLOSE = "CLOSE"


@dataclass(frozen=True, slots=True)
class SpreadLeg:
    symbol: str
    right: OptionRight
    side: Side
    strike: Decimal
    expiry: date
    ratio_qty: int = 1


@dataclass(frozen=True, slots=True)
class UnderwritingProposal:
    """One priced, pre-filtered candidate policy.

    Every monetary field is **per one spread**, with the ×100 contract
    multiplier already applied (§11.2).
    """

    candidate_id: str
    underlying: str
    structure: Structure
    action: Action
    legs: tuple[SpreadLeg, ...]

    short_strike: Decimal
    long_strike: Decimal
    expiry: date
    dte: int

    net_credit: Decimal
    max_profit: Decimal
    max_loss: Decimal
    capital_reserve: Decimal
    breakeven: Decimal
    credit_to_width: Decimal

    p_loss_proxy: Decimal
    p_profit_proxy: Decimal
    expected_value: Decimal
    edge_ratio: Decimal

    liquidity_score: Decimal
    max_leg_spread_pct: Decimal
    short_delta: Decimal
    net_delta: Decimal
    net_vega: Decimal
    greeks_complete: bool

    snapshot_hash: str
    snapshot_as_of: datetime

    @property
    def width(self) -> Decimal:
        return self.short_strike - self.long_strike

    @property
    def proposal_hash(self) -> str:
        """Binds a verdict to exactly this proposal (FR-063, TEST-032).

        Mutating any field below — a strike, the size, the underlying, a leg's
        side — produces a different hash, so a signature minted for the
        original no longer verifies.
        """
        return sha256_of(
            {
                "candidate_id": self.candidate_id,
                "underlying": self.underlying,
                "structure": self.structure,
                "action": self.action,
                "legs": [
                    {
                        "symbol": leg.symbol,
                        "right": leg.right,
                        "side": leg.side,
                        "strike": leg.strike,
                        "expiry": leg.expiry,
                        "ratio_qty": leg.ratio_qty,
                    }
                    for leg in self.legs
                ],
                "short_strike": self.short_strike,
                "long_strike": self.long_strike,
                "expiry": self.expiry,
                "net_credit": self.net_credit,
                "max_loss": self.max_loss,
                "snapshot_hash": self.snapshot_hash,
            }
        )

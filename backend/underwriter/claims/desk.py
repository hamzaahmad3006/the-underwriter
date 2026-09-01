"""The Claims Desk — §11.6, FR-100 … FR-107.

It owns the position after entry, which is where retail options traders
actually lose money. Entering well is the easy half.

§15.4 fixes the precedence and it is strict, not advisory. The order encodes
which reason wins when several are true at once, and the answer is always the
one that removes risk soonest:

1. **Force flat** at 2 DTE — unconditional, regardless of P&L (FR-103).
2. **Stop loss** at 2x the opening credit.
3. **Breach** — the underlying through the short strike.
4. **Profit target** at 50% of the credit captured.
5. Otherwise hold.

Force flat sits first for a reason worth stating: Alpaca publishes no Greeks at
0DTE, so a position held that far becomes unmeasurable by this system's own
risk model. G-08 forbids holding risk it cannot measure, and a profitable
unmeasurable position is still unmeasurable.

Nothing here executes. Every exit it decides on is a proposal routed through
the Kernel like any other (FR-106), and SK-000 is what stops a capital rule
from blocking the very action that reduces capital at risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from underwriter.domain.money import CONTRACT_MULTIPLIER, ZERO

PROFIT_TARGET_PCT = Decimal("0.50")  # FR-101
STOP_LOSS_MULTIPLE = Decimal("2.0")  # FR-102
FORCE_FLAT_DTE = 2  # FR-103


class ExitReason(StrEnum):
    """Why a policy is being closed. Persisted as `settlement_reason` (DB-009)."""

    FORCE_FLAT = "FORCE_FLAT"
    STOP_LOSS = "STOP_LOSS"
    BREACH = "BREACH"
    PROFIT_TARGET = "PROFIT_TARGET"


@dataclass(frozen=True, slots=True)
class ManagedPosition:
    """One open policy as the management cycle sees it."""

    policy_id: str
    policy_number: str
    underlying: str
    contracts: int
    opening_credit: Decimal  # per spread, per share
    max_loss: Decimal  # per spread, in dollars
    short_strike: Decimal
    long_strike: Decimal
    expiry: date
    # Cost to buy the spread back right now, per spread, per share. None when
    # the position cannot currently be priced.
    cost_to_close: Decimal | None = None
    underlying_price: Decimal | None = None

    def dte(self, as_of: date) -> int:
        return (self.expiry - as_of).days

    @property
    def is_breached(self) -> bool:
        """The underlying has traded through the short strike."""
        if self.underlying_price is None:
            return False
        return self.underlying_price <= self.short_strike

    def unrealized_pnl(self) -> Decimal | None:
        """Credit captured so far, in dollars across the whole position."""
        if self.cost_to_close is None:
            return None
        return (
            (self.opening_credit - self.cost_to_close)
            * CONTRACT_MULTIPLIER
            * Decimal(self.contracts)
        )


@dataclass(frozen=True, slots=True)
class ClaimsVerdict:
    """The Claims Desk's finding for one policy. Never an execution."""

    policy_id: str
    should_close: bool
    reason: ExitReason | None
    detail: str
    target_debit: Decimal | None = None
    escalate: bool = False


@dataclass(frozen=True, slots=True)
class ClaimsPolicy:
    """Tunables, all of them from version-controlled config (§29)."""

    profit_target_pct: Decimal = PROFIT_TARGET_PCT
    stop_loss_multiple: Decimal = STOP_LOSS_MULTIPLE
    force_flat_dte: int = FORCE_FLAT_DTE


DEFAULT_CLAIMS_POLICY = ClaimsPolicy()


def evaluate(
    position: ManagedPosition,
    as_of: date,
    policy: ClaimsPolicy = DEFAULT_CLAIMS_POLICY,
) -> ClaimsVerdict:
    """Apply §15.4's precedence to one position. Pure; no I/O, no clock."""
    dte = position.dte(as_of)

    # 1. Force flat. First, and unconditional — no P&L check, no price needed.
    if dte <= policy.force_flat_dte:
        return ClaimsVerdict(
            policy_id=position.policy_id,
            should_close=True,
            reason=ExitReason.FORCE_FLAT,
            detail=(
                f"{dte} DTE is at or inside the {policy.force_flat_dte}-day floor. "
                "Closed unconditionally: Alpaca publishes no Greeks at 0DTE, so "
                "holding further would mean holding risk this system cannot measure (G-08)."
            ),
            # Force flat has to work even when the spread cannot be priced, so
            # the fallback is the width — the most it could possibly cost.
            target_debit=position.cost_to_close
            if position.cost_to_close is not None
            else (position.short_strike - position.long_strike),
        )

    # Everything below needs a price. A position we cannot price is one we
    # cannot judge, so it is escalated rather than silently held.
    if position.cost_to_close is None:
        return ClaimsVerdict(
            policy_id=position.policy_id,
            should_close=False,
            reason=None,
            detail="cost to close is unavailable; cannot evaluate exits this cycle",
            escalate=True,
        )

    stop_level = policy.stop_loss_multiple * position.opening_credit
    target_level = (Decimal("1") - policy.profit_target_pct) * position.opening_credit

    # 2. Stop loss.
    if position.cost_to_close >= stop_level:
        return ClaimsVerdict(
            policy_id=position.policy_id,
            should_close=True,
            reason=ExitReason.STOP_LOSS,
            detail=(
                f"cost to close {position.cost_to_close} reached "
                f"{policy.stop_loss_multiple}x the {position.opening_credit} opening credit"
            ),
            target_debit=position.cost_to_close,
        )

    # 3. Breach — unless the profit target is already met, in which case rule 4
    #    closes it anyway and for a better reason.
    if position.is_breached and position.cost_to_close > target_level:
        return ClaimsVerdict(
            policy_id=position.policy_id,
            should_close=True,
            reason=ExitReason.BREACH,
            detail=(
                f"{position.underlying} at {position.underlying_price} has traded through "
                f"the {position.short_strike} short strike (SK-013)"
            ),
            target_debit=position.cost_to_close,
            escalate=True,  # no new policies on this underlying
        )

    # 4. Profit target.
    if position.cost_to_close <= target_level:
        return ClaimsVerdict(
            policy_id=position.policy_id,
            should_close=True,
            reason=ExitReason.PROFIT_TARGET,
            detail=(
                f"cost to close {position.cost_to_close} is at or below "
                f"{target_level}, capturing {policy.profit_target_pct:%} of the credit"
            ),
            target_debit=position.cost_to_close,
        )

    # 5. Hold.
    return ClaimsVerdict(
        policy_id=position.policy_id,
        should_close=False,
        reason=None,
        detail=(
            f"holding: {dte} DTE, cost to close {position.cost_to_close} between "
            f"the {target_level} target and the {stop_level} stop"
        ),
    )


def realized_pnl(opening_credit: Decimal, closing_debit: Decimal, contracts: int) -> Decimal:
    """FR-107: what the policy actually made or lost, in dollars.

    Positive is a profit. Fees are not modelled — Alpaca paper does not charge
    them, and inventing a number here would make the P&L look more precise than
    it is.
    """
    return (opening_credit - closing_debit) * CONTRACT_MULTIPLIER * Decimal(contracts)


def loss_ratio(claims_paid: Decimal, premiums_written: Decimal) -> Decimal | None:
    """The underwriting measure: losses as a fraction of premium taken.

    Below 1.0 means the desk collected more premium than it paid out. It is
    reported alongside win rate rather than instead of it, because a high hit
    rate with a poor loss ratio is exactly what a badly-run credit book looks
    like.
    """
    if premiums_written <= ZERO:
        return None
    return claims_paid / premiums_written

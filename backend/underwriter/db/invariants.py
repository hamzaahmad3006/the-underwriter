"""DB-INV-1 — the reserve invariant.

    SUM(reserves.amount WHERE status='HELD')
      MUST EQUAL
    SUM(policies.max_loss WHERE status IN ('OPEN','CLOSING'))

Checked every reconcile cycle. A violation means the book's own accounting
disagrees with itself, which is F-25: force MANAGE_ONLY, raise a CRITICAL risk
event, and stop trading until it is repaired from the audit log.

This is the one arithmetic claim the whole solvency story rests on. If reserves
and exposures can drift apart, then SK-001's "60% of NAV deployed" is measuring
something that is not the real exposure, and every limit above it is decorative.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from underwriter.db.models import Policy, Reserve
from underwriter.domain.money import ZERO

OPEN_STATUSES = ("OPEN", "CLOSING")


@dataclass(frozen=True, slots=True)
class ReserveInvariant:
    holds: bool
    held_reserves: Decimal
    exposed_max_loss: Decimal
    difference: Decimal
    detail: str


def check_reserve_invariant(session: Session) -> ReserveInvariant:
    """Compare held reserves against live exposure.

    Summed in Python rather than by the database on purpose: the amounts are
    stored as decimal strings (NFR-013), and asking SQLite to SUM them would
    coerce to float — reintroducing exactly the rounding this schema exists to
    avoid, in the one check meant to catch drift.
    """
    held = session.execute(select(Reserve.amount).where(Reserve.status == "HELD")).scalars().all()
    exposed = (
        session.execute(select(Policy.max_loss).where(Policy.status.in_(OPEN_STATUSES)))
        .scalars()
        .all()
    )

    held_total = sum((amount for amount in held if amount is not None), ZERO)
    exposed_total = sum((loss for loss in exposed if loss is not None), ZERO)
    difference = held_total - exposed_total

    if difference == ZERO:
        return ReserveInvariant(
            holds=True,
            held_reserves=held_total,
            exposed_max_loss=exposed_total,
            difference=ZERO,
            detail=f"{len(held)} held reserves match {len(exposed)} exposed policies",
        )

    direction = "over" if difference > ZERO else "under"
    return ReserveInvariant(
        holds=False,
        held_reserves=held_total,
        exposed_max_loss=exposed_total,
        difference=difference,
        detail=(
            f"reserves are {direction}-held by {abs(difference)}: "
            f"held={held_total} vs exposed={exposed_total} "
            f"({len(held)} reserves, {len(exposed)} policies)"
        ),
    )

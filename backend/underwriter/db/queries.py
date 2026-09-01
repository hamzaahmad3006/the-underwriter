"""Read models for the dashboard.

Aggregates live here rather than in controllers so the overview, the risk page
and the equity curve cannot each compute "deployed capital" a slightly
different way — which is exactly how two panels come to disagree on the same
screen.

Everything sums in Python. The amounts are decimal strings (NFR-013), and
asking SQLite to SUM them would coerce to float, putting a rounding error into
the numbers a judge reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from underwriter.db.models import Account, PnlRecord, Policy, Reserve, RiskEvent
from underwriter.domain.money import ZERO

OPEN_STATUSES = ("OPEN", "CLOSING")
SETTLED = "SETTLED"


def _sum(values: list[Decimal | None]) -> Decimal:
    return sum((v for v in values if v is not None), ZERO)


@dataclass(frozen=True, slots=True)
class BookSummary:
    """The shape §21.1's tiles read from."""

    as_of: datetime

    baseline_equity: Decimal | None
    equity: Decimal | None
    reserved: Decimal
    available: Decimal | None
    at_risk_pct: Decimal

    realized_pnl: Decimal
    unrealized_pnl: Decimal

    open_policies: int
    closed_policies: int
    policies_written: int

    wins: int
    losses: int
    win_rate: Decimal | None
    loss_ratio: Decimal | None
    premiums_written: Decimal
    claims_paid: Decimal

    open_risk_events: int


def book_summary(session: Session, *, deployable_pct: Decimal = Decimal("0.60")) -> BookSummary:
    """One read, every headline figure — computed the same way everywhere."""
    account = session.execute(select(Account).limit(1)).scalar_one_or_none()
    policies = session.execute(select(Policy)).scalars().all()
    reserves = (
        session.execute(select(Reserve.amount).where(Reserve.status == "HELD")).scalars().all()
    )
    latest_pnl = session.execute(
        select(PnlRecord).order_by(PnlRecord.recorded_at.desc()).limit(1)
    ).scalar_one_or_none()
    open_events = (
        session.execute(select(RiskEvent).where(RiskEvent.resolved_at.is_(None))).scalars().all()
    )

    open_policies = [p for p in policies if p.status in OPEN_STATUSES]
    settled = [p for p in policies if p.status == SETTLED]

    reserved = _sum(list(reserves))
    equity = latest_pnl.equity if latest_pnl else (account.baseline_equity if account else None)

    realized = _sum([p.realized_pnl for p in settled])
    wins = [p for p in settled if (p.realized_pnl or ZERO) > ZERO]
    losses = [p for p in settled if (p.realized_pnl or ZERO) <= ZERO]

    # The underwriting measure: claims paid against premium written. Reported
    # alongside win rate rather than instead of it, because a high hit rate
    # with a poor loss ratio is what a badly-run credit book looks like.
    premiums = _sum([p.opening_credit for p in settled])
    claims = _sum([-(p.realized_pnl or ZERO) for p in losses])

    at_risk = ZERO
    if equity and equity > ZERO:
        at_risk = (reserved / equity * Decimal("100")).quantize(Decimal("0.01"))

    return BookSummary(
        as_of=datetime.now(UTC),
        baseline_equity=account.baseline_equity if account else None,
        equity=equity,
        reserved=reserved,
        available=(equity - reserved) if equity is not None else None,
        at_risk_pct=at_risk,
        realized_pnl=realized,
        unrealized_pnl=latest_pnl.unrealized_pnl or ZERO if latest_pnl else ZERO,
        open_policies=len(open_policies),
        closed_policies=len(settled),
        policies_written=len(policies),
        wins=len(wins),
        losses=len(losses),
        win_rate=(
            (Decimal(len(wins)) / Decimal(len(settled))).quantize(Decimal("0.001"))
            if settled
            else None
        ),
        loss_ratio=((claims / premiums).quantize(Decimal("0.001")) if premiums > ZERO else None),
        premiums_written=premiums,
        claims_paid=claims,
        open_risk_events=len(open_events),
    )


@dataclass(frozen=True, slots=True)
class ExposureSummary:
    """§21.3 — where the risk actually sits right now."""

    as_of: datetime
    total_reserved: Decimal
    portfolio_max_loss: Decimal
    by_underlying: dict[str, Decimal]
    open_policies: int
    reserve_invariant_holds: bool
    reserve_invariant_detail: str


def exposure_summary(session: Session) -> ExposureSummary:
    """Concentration and reserve health, including DB-INV-1."""
    from underwriter.db.invariants import check_reserve_invariant

    open_policies = (
        session.execute(select(Policy).where(Policy.status.in_(OPEN_STATUSES))).scalars().all()
    )

    by_underlying: dict[str, Decimal] = {}
    for policy in open_policies:
        current = by_underlying.get(policy.underlying, ZERO)
        by_underlying[policy.underlying] = current + (policy.capital_reserve or ZERO)

    invariant = check_reserve_invariant(session)

    return ExposureSummary(
        as_of=datetime.now(UTC),
        total_reserved=invariant.held_reserves,
        portfolio_max_loss=invariant.exposed_max_loss,
        by_underlying=by_underlying,
        open_policies=len(open_policies),
        reserve_invariant_holds=invariant.holds,
        reserve_invariant_detail=invariant.detail,
    )


def equity_curve(session: Session, *, limit: int = 500) -> list[PnlRecord]:
    """DB-014 in time order, oldest first, for the chart."""
    rows = (
        session.execute(select(PnlRecord).order_by(PnlRecord.recorded_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return list(reversed(rows))

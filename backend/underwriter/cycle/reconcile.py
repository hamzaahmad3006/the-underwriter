"""The reconciliation cycle — §12.3, FR-089, ERR-007, F-19, F-25.

Every five minutes: ask the broker what it actually holds, compare it against
what the book believes, record the equity point, and check DB-INV-1.

The asymmetry here is deliberate. A position the broker holds and the book
cannot explain is an **orphan** and always CRITICAL — it is naked exposure
nobody is managing, and F-19 forces MANAGE_ONLY until someone resolves it. A
policy the book holds and the broker does not is the milder direction: it
usually means a fill never happened, so the policy is flagged rather than the
system halted.

DB-INV-1 is checked here because this is the only cycle that runs often enough
to catch drift early. If held reserves stop matching exposed max loss, every
capital limit above is measuring the wrong number, and F-25 stops trading until
it is repaired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from underwriter.audit.ledger import Actor, append
from underwriter.cycle.recorder import CycleRecorder
from underwriter.cycle.underwrite import CycleStatus, new_correlation_id
from underwriter.db import session_scope
from underwriter.db.invariants import check_reserve_invariant
from underwriter.db.models import PnlRecord, Policy, PolicyLeg, PositionSnapshot
from underwriter.db.queries import book_summary
from underwriter.domain.money import ZERO
from underwriter.execution.ports import BrokerPort, BrokerPosition

OPEN_STATUSES = ("OPEN", "CLOSING", "LEG_RISK")


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    correlation_id: str
    status: CycleStatus
    broker_positions: int = 0
    book_policies: int = 0
    orphans: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    invariant_holds: bool = True
    invariant_detail: str = ""
    equity: Decimal | None = None
    forced_manage_only: bool = False
    detail: str = ""
    started_at: datetime = datetime(1970, 1, 1, tzinfo=UTC)
    finished_at: datetime | None = None
    events: tuple[str, ...] = field(default_factory=tuple)

    @property
    def healthy(self) -> bool:
        return not self.orphans and self.invariant_holds


def run_reconcile_cycle(
    broker: BrokerPort,
    *,
    equity: Decimal | None = None,
    correlation_id: str | None = None,
) -> ReconcileReport:
    """Compare the broker against the book. Never raises into the scheduler."""
    started = datetime.now(UTC)
    cid = correlation_id or new_correlation_id()

    try:
        positions = broker.list_positions()
    except Exception as exc:
        return ReconcileReport(
            correlation_id=cid,
            status=CycleStatus.ERROR,
            detail=f"could not read broker positions: {type(exc).__name__}: {exc}",
            started_at=started,
            finished_at=datetime.now(UTC),
        )

    held = {position.symbol: position for position in positions if position.qty != ZERO}

    with session_scope() as session:
        recorder = CycleRecorder(session, cid)

        open_policies = (
            session.execute(select(Policy).where(Policy.status.in_(OPEN_STATUSES))).scalars().all()
        )
        policy_ids = [policy.id for policy in open_policies]
        legs = (
            session.execute(select(PolicyLeg).where(PolicyLeg.policy_id.in_(policy_ids)))
            .scalars()
            .all()
            if policy_ids
            else []
        )
        policy_by_symbol = {leg.option_symbol: leg.policy_id for leg in legs}

        taken_at = datetime.now(UTC)
        for symbol, position in held.items():
            session.add(
                PositionSnapshot(
                    taken_at=taken_at,
                    symbol=symbol,
                    qty=position.qty,
                    avg_entry_price=position.avg_entry_price,
                    market_value=position.market_value,
                    unrealized_pl=position.unrealized_pl,
                    matched_policy_id=policy_by_symbol.get(symbol),
                )
            )

        orphans = tuple(symbol for symbol in held if symbol not in policy_by_symbol)
        missing = tuple(symbol for symbol in policy_by_symbol if symbol not in held)

        events: list[str] = []
        for symbol in orphans:
            # F-19: the broker holds something no policy explains. Always
            # CRITICAL — it is exposure nobody is managing.
            recorder.risk_event(
                "ORPHAN_POSITION",
                "CRITICAL",
                detail={"symbol": symbol, "qty": str(held[symbol].qty)},
            )
            events.append(f"orphan: {symbol}")

        for symbol in missing:
            # The milder direction: usually a fill that never happened.
            recorder.risk_event(
                "RECONCILE_DIVERGENCE",
                "WARN",
                policy_id=policy_by_symbol.get(symbol),
                detail={"symbol": symbol, "detail": "the book holds a leg the broker does not"},
            )
            events.append(f"missing: {symbol}")

        invariant = check_reserve_invariant(session)
        if not invariant.holds:
            # F-25: the book's own accounting disagrees with itself.
            recorder.risk_event(
                "RESERVE_INVARIANT",
                "CRITICAL",
                detail={"detail": invariant.detail},
            )
            events.append("DB-INV-1 violated")

        book = book_summary(session)
        recorded_equity = equity if equity is not None else book.equity
        if recorded_equity is not None:
            session.add(
                PnlRecord(
                    recorded_at=taken_at,
                    equity=recorded_equity,
                    realized_pnl_cum=book.realized_pnl,
                    unrealized_pnl=_unrealized(list(held.values())),
                    open_policies=book.open_policies,
                    closed_policies=book.closed_policies,
                    win_rate=book.win_rate,
                    loss_ratio=book.loss_ratio,
                )
            )

        append(
            session,
            actor=Actor.SCHEDULER,
            action="RECONCILED",
            after={
                "broker_positions": len(held),
                "book_policies": len(open_policies),
                "orphans": list(orphans),
                "missing": list(missing),
                "invariant_holds": invariant.holds,
            },
            correlation_id=cid,
        )

        forced = bool(orphans) or not invariant.holds

        return ReconcileReport(
            correlation_id=cid,
            status=CycleStatus.SUCCESS if not forced else CycleStatus.ABORTED,
            broker_positions=len(held),
            book_policies=len(open_policies),
            orphans=orphans,
            missing=missing,
            invariant_holds=invariant.holds,
            invariant_detail=invariant.detail,
            equity=recorded_equity,
            # F-19 and F-25 both force MANAGE_ONLY. The caller applies it; this
            # cycle reports rather than mutates, so one place owns the mode.
            forced_manage_only=forced,
            detail=(
                f"{len(held)} broker positions against {len(open_policies)} open policies; "
                f"{len(orphans)} orphaned, {len(missing)} missing"
            ),
            started_at=started,
            finished_at=datetime.now(UTC),
            events=tuple(events),
        )


def _unrealized(positions: list[BrokerPosition]) -> Decimal:
    return sum((p.unrealized_pl for p in positions if p.unrealized_pl is not None), ZERO)

"""Wiring the desk together — ERR-007, OPS-020, TD-01.

This is the only module that constructs the real dependencies: the Alpaca data
source, the Groq client, the broker, the Execution Engine. Everything else in
the system takes them as arguments, which is why the whole pipeline is testable
without any of them.

Two boot rules, both from ERR-007:

* **The desk boots in `MANAGE_ONLY`, always.** Not because a crash is likely,
  but because after one the book and the broker may disagree, and the first
  cycle must not open a new position on top of a divergence nobody has looked
  at yet. Reconciliation runs, and an operator promotes to `ACTIVE`.
* **A missing dependency disables its cycle rather than the process.** No Groq
  key means no underwriting cycle — but the management and reconcile cycles
  still run, because an open book still needs managing whether or not a model
  is available to write new policies.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from underwriter.agent.client import GroqClient, LLMUnavailableError
from underwriter.agent.underwriter import AIUnderwriter
from underwriter.claims.desk import ManagedPosition
from underwriter.cycle.manage import run_management_cycle
from underwriter.cycle.reconcile import run_reconcile_cycle
from underwriter.cycle.scheduler import CycleScheduler, JobResult
from underwriter.cycle.underwrite import CycleStatus, run_underwriting_cycle
from underwriter.data.alpaca_source import AlpacaMarketData
from underwriter.data.credentials import has_credentials
from underwriter.data.snapshot import DEFAULT_SNAPSHOT_CONFIG, SnapshotConfig
from underwriter.db import session_scope
from underwriter.db.models import Policy, PolicyLeg, SystemConfig
from underwriter.db.queries import book_summary
from underwriter.domain.money import ZERO
from underwriter.domain.proposal import Structure, UnderwritingProposal
from underwriter.execution.alpaca_broker import AlpacaBroker
from underwriter.execution.engine import ExecutionEngine
from underwriter.kernel.context import AccountState, KernelContext, OpenPolicy, SystemMode

log = logging.getLogger(__name__)

BOOT_MODE = SystemMode.MANAGE_ONLY  # ERR-007


@dataclass(frozen=True, slots=True)
class Wiring:
    """What could actually be constructed, and what could not."""

    scheduler: CycleScheduler | None
    can_underwrite: bool
    can_execute: bool
    notes: tuple[str, ...]


def ensure_system_config() -> None:
    """DB-001, and ERR-007's boot mode.

    Boot is MANAGE_ONLY every time, including a clean restart. Promoting to
    ACTIVE is an operator action so that someone has looked at the book after
    the process came back.
    """
    with session_scope() as session:
        config = session.get(SystemConfig, 1)
        if config is None:
            session.add(
                SystemConfig(
                    id=1,
                    mode=str(BOOT_MODE),
                    strategy_profile=os.environ.get("STRATEGY_PROFILE", "PERFORMANCE"),
                    updated_by="BOOT",
                )
            )
            return

        if config.mode != str(BOOT_MODE):
            config.mode = str(BOOT_MODE)
            config.updated_at = datetime.now(UTC)
            config.updated_by = "BOOT"


def current_mode() -> SystemMode:
    with session_scope() as session:
        config = session.get(SystemConfig, 1)
        return SystemMode(config.mode) if config else BOOT_MODE


def build_kernel_context(*, now: datetime | None = None) -> KernelContext:
    """The book as the Kernel sees it, read fresh from the database.

    SK-P5 wants account state read at decision time rather than cached. The
    equity here comes from the last reconciliation, which is as fresh as the
    five-minute cycle makes it.
    """
    moment = now or datetime.now(UTC)

    with session_scope() as session:
        book = book_summary(session)
        config = session.get(SystemConfig, 1)
        rows = (
            session.execute(select(Policy).where(Policy.status.in_(("OPEN", "CLOSING"))))
            .scalars()
            .all()
        )

        open_policies = tuple(
            OpenPolicy(
                policy_id=row.id,
                underlying=row.underlying,
                structure=Structure.PUT_CREDIT_SPREAD,
                short_strike=ZERO,
                long_strike=ZERO,
                expiry=moment.date(),
                contracts=row.contracts,
                max_loss=row.max_loss or ZERO,
                reserve=row.capital_reserve or ZERO,
            )
            for row in rows
        )

        equity = book.equity or ZERO
        return KernelContext(
            now=moment,
            account=AccountState(
                nav=equity,
                equity=equity,
                buying_power=equity * Decimal("2"),
                peak_equity=(config.peak_equity if config and config.peak_equity else equity),
                daily_realized_pnl=book.realized_pnl,
                as_of=moment,
                read_ok=equity > ZERO,
            ),
            data_as_of=moment,
            mode=SystemMode(config.mode) if config else BOOT_MODE,
            kill_switch_engaged=bool(config.kill_switch) if config else False,
            open_policies=open_policies,
        )


def open_positions() -> tuple[tuple[ManagedPosition, ...], dict[str, UnderwritingProposal]]:
    """What the Claims Desk manages this cycle."""
    with session_scope() as session:
        rows = (
            session.execute(select(Policy).where(Policy.status.in_(("OPEN", "CLOSING"))))
            .scalars()
            .all()
        )
        legs = {
            leg.policy_id: leg
            for leg in session.execute(select(PolicyLeg)).scalars().all()
            if leg.side == "SELL"
        }

        positions = tuple(
            ManagedPosition(
                policy_id=row.id,
                policy_number=row.policy_number,
                underlying=row.underlying,
                contracts=row.contracts,
                opening_credit=row.opening_credit or ZERO,
                max_loss=row.max_loss or ZERO,
                short_strike=(legs[row.id].strike or ZERO) if row.id in legs else ZERO,
                long_strike=ZERO,
                expiry=datetime.fromisoformat(row.expiration).date()
                if row.expiration
                else datetime.now(UTC).date(),
                # Live cost to close needs a quote the management cycle does not
                # yet fetch; absent, the Claims Desk escalates rather than
                # guessing, and force-flat still works on DTE alone.
                cost_to_close=None,
            )
            for row in rows
            if row.expiration
        )
        # Closing orders need the original proposal to build the mirror mleg.
        # Reconstructing it from stored legs is the next piece of work; until
        # then the management cycle evaluates and reports, and an exit that
        # cannot be constructed says so rather than silently doing nothing.
        return positions, {}


def build(*, snapshot_config: SnapshotConfig = DEFAULT_SNAPSHOT_CONFIG) -> Wiring:
    """Construct what the environment allows, and say what it did not."""
    notes: list[str] = []
    secret = os.environ.get("KERNEL_SIGNING_SECRET", "").strip()

    if not secret:
        return Wiring(None, False, False, ("KERNEL_SIGNING_SECRET is unset; no cycle can run",))

    source = None
    if has_credentials():
        try:
            source = AlpacaMarketData()
        except Exception as exc:
            notes.append(f"market data unavailable: {exc}")
    else:
        notes.append("no Alpaca credentials; market data and reconciliation are disabled")

    agent = None
    try:
        agent = AIUnderwriter(GroqClient())
    except (LLMUnavailableError, ValueError) as exc:
        notes.append(f"no AI Underwriter: {exc}")

    broker = None
    try:
        broker = AlpacaBroker()
    except Exception as exc:
        notes.append(f"no execution: {exc}")

    execution = ExecutionEngine(broker, secret=secret) if broker is not None else None

    def underwrite() -> JobResult:
        if source is None or agent is None:
            return JobResult(CycleStatus.NO_ACTION, "UNDERWRITING_DISABLED", "; ".join(notes))

        context = build_kernel_context()
        if context.mode is not SystemMode.ACTIVE:
            # ERR-007: no entries until an operator has promoted the desk.
            return JobResult(
                CycleStatus.NO_ACTION,
                "MODE_BLOCKED",
                f"mode is {context.mode}; entries require ACTIVE",
            )

        report = run_underwriting_cycle(
            source=source,
            agent=agent,
            context=context,
            secret=secret,
            execution=execution,
            snapshot_config=snapshot_config,
            persist=True,
        )
        return JobResult(report.status, report.outcome, report.detail, report.correlation_id)

    def manage() -> JobResult:
        positions, proposals = open_positions()
        if not positions:
            return JobResult(CycleStatus.NO_ACTION, "NO_OPEN_POLICIES", "the book is empty")

        report = run_management_cycle(
            positions,
            proposals_by_policy=proposals,
            context=build_kernel_context(),
            secret=secret,
            execution=execution,
        )
        return JobResult(report.status, "MANAGED", report.detail, report.correlation_id)

    def reconcile() -> JobResult:
        if broker is None:
            return JobResult(CycleStatus.NO_ACTION, "RECONCILE_DISABLED", "; ".join(notes))

        report = run_reconcile_cycle(broker)
        if report.forced_manage_only:
            _force_manage_only(report.detail)

        return JobResult(report.status, "RECONCILED", report.detail, report.correlation_id)

    scheduler = CycleScheduler(underwrite=underwrite, manage=manage, reconcile=reconcile)
    return Wiring(
        scheduler=scheduler,
        can_underwrite=source is not None and agent is not None,
        can_execute=execution is not None,
        notes=tuple(notes),
    )


def _force_manage_only(reason: str) -> None:
    """F-19 and F-25 both land here. One place owns the mode."""
    with session_scope() as session:
        config = session.get(SystemConfig, 1)
        if config is None or config.mode == str(SystemMode.MANAGE_ONLY):
            return
        config.mode = str(SystemMode.MANAGE_ONLY)
        config.updated_at = datetime.now(UTC)
        config.updated_by = "RECONCILE"
        log.error("forced MANAGE_ONLY: %s", reason)

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
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from underwriter.agent.client import GroqClient, LLMUnavailableError
from underwriter.agent.underwriter import AIUnderwriter
from underwriter.audit.ledger import Actor, append
from underwriter.claims.desk import ManagedPosition
from underwriter.claims.pricing import price_position, underlying_price
from underwriter.cycle.manage import run_management_cycle
from underwriter.cycle.reconcile import run_reconcile_cycle
from underwriter.cycle.scheduler import CycleScheduler, JobResult
from underwriter.cycle.underwrite import CycleStatus, run_underwriting_cycle
from underwriter.data.alpaca_source import AlpacaMarketData
from underwriter.data.credentials import has_credentials
from underwriter.data.ports import MarketDataSource
from underwriter.data.snapshot import DEFAULT_SNAPSHOT_CONFIG, SnapshotConfig
from underwriter.db import session_scope
from underwriter.db.models import Account, Policy, PolicyLeg, SystemConfig
from underwriter.db.queries import book_summary
from underwriter.domain.market import OptionRight, Side
from underwriter.domain.money import ZERO
from underwriter.domain.proposal import (
    Action,
    SpreadLeg,
    Structure,
    UnderwritingProposal,
)
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


def ensure_account() -> str | None:
    """ALP-001, ALP-002 — record the account and its baseline equity at boot.

    Without this row the Kernel has no account state to read, and SK-025
    refuses every proposal including a close. That is correct of SK-025 and
    wrong of us: a desk that comes back up holding positions must be able to
    manage them before the first reconciliation lands.

    The baseline is written once and never updated. It is the fixed point every
    drawdown and equity-curve figure is measured against, so a "helpful" refresh
    would quietly reset the drawdown the desk is being judged on.
    """
    broker = build_broker()
    if broker is None:
        return None

    try:
        raw = broker._client.get_account()
        account_id = str(getattr(raw, "account_number", "") or getattr(raw, "id", ""))
        equity = Decimal(str(getattr(raw, "equity", "0")))
    except Exception as exc:
        log.warning("could not read the account for provisioning: %s", exc)
        return None

    if not account_id or equity <= ZERO:
        return None

    with session_scope() as session:
        existing = session.execute(
            select(Account).where(Account.alpaca_account_id == account_id)
        ).scalar_one_or_none()

        if existing is not None:
            return existing.id

        row = Account(
            alpaca_account_id=account_id,
            is_paper=1,  # the schema CHECKs this; a live account cannot be stored
            baseline_equity=equity,
            baseline_at=datetime.now(UTC),
        )
        session.add(row)
        session.flush()

        append(
            session,
            actor=Actor.SCHEDULER,
            action="ACCOUNT_PROVISIONED",
            entity_type="account",
            entity_id=row.id,
            after={"alpaca_account_id": account_id, "baseline_equity": str(equity)},
        )
        log.info("recorded account %s with baseline equity %s", account_id, equity)
        return row.id


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


def rebuild_proposal(policy: Policy, legs: list[PolicyLeg]) -> UnderwritingProposal | None:
    """Reconstruct the proposal a policy was written from.

    The closing order is the mirror of the opening one, so it needs the same
    legs, in the same order, with the same symbols. Rebuilding from stored rows
    rather than keeping the object alive between cycles is deliberate: the
    process restarts, and a book that can only be managed by a process that has
    been up since the trade was written is not a book that can be managed.
    """
    short = next((leg for leg in legs if leg.side == "SELL"), None)
    long = next((leg for leg in legs if leg.side == "BUY"), None)
    if short is None or long is None or not policy.expiration:
        return None

    expiry = date.fromisoformat(policy.expiration)
    per_spread = Decimal(policy.contracts or 1)
    max_loss = (
        (policy.max_loss or ZERO) / per_spread if per_spread > ZERO else (policy.max_loss or ZERO)
    )

    spread_legs = (
        SpreadLeg(
            symbol=short.option_symbol,
            right=OptionRight.PUT,
            side=Side.SELL,
            strike=short.strike or ZERO,
            expiry=expiry,
            ratio_qty=short.ratio_qty or 1,
        ),
        SpreadLeg(
            symbol=long.option_symbol,
            right=OptionRight.PUT,
            side=Side.BUY,
            strike=long.strike or ZERO,
            expiry=expiry,
            ratio_qty=long.ratio_qty or 1,
        ),
    )

    credit = policy.opening_credit or ZERO
    return UnderwritingProposal(
        # SK-024 checks membership against the set supplied for this action, and
        # the management cycle supplies exactly this one.
        candidate_id=f"exit:{policy.id}",
        underlying=policy.underlying,
        structure=Structure.PUT_CREDIT_SPREAD,
        action=Action.CLOSE,
        legs=spread_legs,
        short_strike=short.strike or ZERO,
        long_strike=long.strike or ZERO,
        expiry=expiry,
        dte=(expiry - datetime.now(UTC).date()).days,
        net_credit=credit,
        max_profit=(policy.max_profit or ZERO) / per_spread if per_spread > ZERO else ZERO,
        max_loss=max_loss,
        capital_reserve=max_loss,
        breakeven=(short.strike or ZERO) - credit,
        credit_to_width=ZERO,
        p_loss_proxy=ZERO,
        p_profit_proxy=ZERO,
        expected_value=ZERO,
        edge_ratio=ZERO,
        liquidity_score=ZERO,
        max_leg_spread_pct=ZERO,
        short_delta=short.open_delta or ZERO,
        net_delta=ZERO,
        net_vega=ZERO,
        greeks_complete=True,
        snapshot_hash=f"exit:{policy.id}",
        snapshot_as_of=datetime.now(UTC),
    )


def open_positions(
    source: MarketDataSource | None = None,
) -> tuple[tuple[ManagedPosition, ...], dict[str, UnderwritingProposal]]:
    """What the Claims Desk manages this cycle, priced where possible.

    Without a live cost to close, only force flat can fire — the profit target
    and the stop both compare against one. So the chain is fetched per policy
    when a data source is available, and a position that still cannot be priced
    is escalated rather than quietly held.
    """
    with session_scope() as session:
        rows = (
            session.execute(select(Policy).where(Policy.status.in_(("OPEN", "CLOSING"))))
            .scalars()
            .all()
        )
        if not rows:
            return (), {}

        policy_ids = [row.id for row in rows]
        all_legs = (
            session.execute(select(PolicyLeg).where(PolicyLeg.policy_id.in_(policy_ids)))
            .scalars()
            .all()
        )
        legs_by_policy: dict[str, list[PolicyLeg]] = {}
        for leg in all_legs:
            legs_by_policy.setdefault(leg.policy_id, []).append(leg)

        positions: list[ManagedPosition] = []
        proposals: dict[str, UnderwritingProposal] = {}

        for row in rows:
            legs = legs_by_policy.get(row.id, [])
            proposal = rebuild_proposal(row, legs)
            if proposal is not None:
                proposals[row.id] = proposal

            short = next((leg for leg in legs if leg.side == "SELL"), None)
            long = next((leg for leg in legs if leg.side == "BUY"), None)

            quote = None
            price = None
            if source is not None and short is not None and long is not None and row.expiration:
                quote = price_position(
                    source,
                    underlying=row.underlying,
                    expiry=date.fromisoformat(row.expiration),
                    short_symbol=short.option_symbol,
                    long_symbol=long.option_symbol,
                )
                price = underlying_price(source, row.underlying)

            positions.append(
                ManagedPosition(
                    policy_id=row.id,
                    policy_number=row.policy_number,
                    underlying=row.underlying,
                    contracts=row.contracts,
                    opening_credit=row.opening_credit or ZERO,
                    max_loss=row.max_loss or ZERO,
                    short_strike=(short.strike or ZERO) if short else ZERO,
                    long_strike=(long.strike or ZERO) if long else ZERO,
                    expiry=(
                        date.fromisoformat(row.expiration)
                        if row.expiration
                        else datetime.now(UTC).date()
                    ),
                    cost_to_close=quote.cost_to_close if quote else None,
                    underlying_price=price,
                )
            )

        return tuple(positions), proposals


def signing_secret() -> str:
    """The Kernel signing key. Absent means nothing can be authorised at all."""
    secret = os.environ.get("KERNEL_SIGNING_SECRET", "").strip()
    if not secret:
        raise RuntimeError("KERNEL_SIGNING_SECRET is unset; no verdict can be minted")
    return secret


def build_broker() -> AlpacaBroker | None:
    """A trading-credentialed broker, or None. Read paths tolerate None."""
    try:
        return AlpacaBroker()
    except Exception:
        return None


def force_manage_only(reason: str) -> None:
    """Public name for the mode forcing that F-19 and F-25 both land on."""
    _force_manage_only(reason)


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

    from underwriter.cli import is_available as cli_available
    from underwriter.cli import validate_order

    if broker is None:
        execution = None
    else:
        # ALP-007: a second implementation checks the payload before the first
        # one sends it. Absent CLI means no check, never a blocked cycle.
        execution = ExecutionEngine(
            broker,
            secret=secret,
            preflight=validate_order if cli_available() else None,
        )
        if not cli_available():
            notes.append("the Alpaca CLI is not installed; order pre-flight is skipped")

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
        positions, proposals = open_positions(source)
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

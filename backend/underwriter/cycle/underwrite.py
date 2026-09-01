"""The underwriting cycle — the pipeline of §12.1, end to end.

    Market Data -> Actuary -> AI Underwriter -> Solvency Kernel -> Execution

This module is deliberately thin. Every step it calls already refuses on its
own terms; the cycle's job is to run them in order, record what happened, and
stop at the first refusal. It contains no risk logic of its own, because a
second place that decides things is a second place that can decide wrongly.

One correlation id threads the whole cycle, so the snapshot, the candidates,
the decision, the verdict and the order are one story in the audit log rather
than five rows that share a timestamp.

FR-026 governs the shape of the result: a cycle that writes nothing is a
successful cycle. `NO_ACTION` is a distinct outcome from `ERROR` and most
cycles will end in it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from underwriter.actuary.engine import price_put_credit_spreads
from underwriter.agent.prompt import PortfolioContext
from underwriter.agent.underwriter import AIUnderwriter, Outcome
from underwriter.data.ports import MarketDataSource
from underwriter.data.snapshot import DEFAULT_SNAPSHOT_CONFIG, SnapshotConfig, build_snapshot
from underwriter.domain.money import ZERO
from underwriter.execution.engine import ExecutionEngine, ExecutionStatus
from underwriter.kernel import kernel
from underwriter.kernel.context import KernelContext
from underwriter.kernel.limits import DEFAULT_LIMITS, KernelLimits


class CycleStatus(StrEnum):
    """DB-020's status vocabulary. `NO_ACTION` is a success (FR-026)."""

    SUCCESS = "SUCCESS"
    NO_ACTION = "NO_ACTION"
    ABORTED = "ABORTED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class CycleReport:
    """Everything one cycle did, in the order it did it."""

    correlation_id: str
    status: CycleStatus
    outcome: str
    detail: str = ""
    started_at: datetime = datetime(1970, 1, 1, tzinfo=UTC)
    finished_at: datetime | None = None

    snapshot_hash: str | None = None
    candidates_priced: int = 0
    candidates_discarded: int = 0
    decision: str | None = None
    verdict: str | None = None
    approved_contracts: int = 0
    reject_reasons: tuple[str, ...] = ()
    execution_status: str | None = None
    steps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)

    @property
    def traded(self) -> bool:
        return self.execution_status == ExecutionStatus.FILLED


def new_correlation_id() -> str:
    return f"cyc_{uuid.uuid4().hex[:16]}"


def run_underwriting_cycle(
    *,
    source: MarketDataSource,
    agent: AIUnderwriter,
    context: KernelContext,
    secret: str,
    execution: ExecutionEngine | None = None,
    snapshot_config: SnapshotConfig = DEFAULT_SNAPSHOT_CONFIG,
    limits: KernelLimits = DEFAULT_LIMITS,
    dry_run: bool = False,
    correlation_id: str | None = None,
) -> CycleReport:
    """Run one entry cycle. Never raises; every failure is a recorded outcome.

    `dry_run=True` runs the whole pipeline to a Kernel verdict and transmits
    nothing (API-032). It is the safe demo trigger, and it exercises exactly
    the same code path as a live cycle up to the point of execution.
    """
    started = datetime.now(UTC)
    cid = correlation_id or new_correlation_id()
    steps: list[str] = []

    def done(status: CycleStatus, outcome: str, detail: str = "", **extra: Any) -> CycleReport:
        return CycleReport(
            correlation_id=cid,
            status=status,
            outcome=outcome,
            detail=detail,
            started_at=started,
            finished_at=datetime.now(UTC),
            steps=tuple(steps),
            **extra,
        )

    try:
        # 1. Market data ---------------------------------------------------
        steps.append("fetch_snapshot")
        snapshot_result = build_snapshot(source, config=snapshot_config)

        if not snapshot_result.ok or snapshot_result.snapshot is None:
            return done(
                CycleStatus.ABORTED,
                str(snapshot_result.aborted),
                snapshot_result.detail,
            )

        snapshot = snapshot_result.snapshot

        # 2. Actuary -------------------------------------------------------
        steps.append("price_candidates")
        priced = price_put_credit_spreads(snapshot, universe=snapshot_config.universe)

        if priced.is_empty:
            return done(
                CycleStatus.NO_ACTION,
                "NO_QUALIFYING_CANDIDATES",
                f"{len(priced.discards)} candidates discarded by the Actuary",
                snapshot_hash=snapshot.snapshot_hash,
                candidates_discarded=len(priced.discards),
            )

        # 3. AI Underwriter ------------------------------------------------
        steps.append("underwrite")
        portfolio = _portfolio_context(context, limits)
        underwriting = agent.decide(priced.proposals, portfolio)

        base: dict[str, Any] = {
            "snapshot_hash": snapshot.snapshot_hash,
            "candidates_priced": len(priced.proposals),
            "candidates_discarded": len(priced.discards),
            "decision": str(underwriting.outcome),
        }

        if underwriting.outcome is Outcome.ABORTED:
            return done(
                CycleStatus.ABORTED,
                str(underwriting.abort_reason),
                underwriting.detail,
                **base,
            )

        if underwriting.outcome is not Outcome.WRITE or underwriting.selected is None:
            return done(CycleStatus.NO_ACTION, "DECLINED", underwriting.detail, **base)

        # 4. Solvency Kernel ------------------------------------------------
        steps.append("adjudicate")
        proposal = underwriting.selected
        requested = underwriting.decision.contracts if underwriting.decision else 0

        # SK-024 checks membership against the set actually supplied, so the
        # context is narrowed to exactly what the Actuary produced this cycle.
        adjudicated = kernel.evaluate(
            proposal,
            requested_contracts=requested or 0,
            context=_with_candidates(context, priced.proposals),
            secret=secret,
            limits=limits,
        )

        base |= {
            "verdict": str(adjudicated.verdict),
            "approved_contracts": adjudicated.approved_contracts,
            "reject_reasons": tuple(adjudicated.reject_reasons),
        }

        if not adjudicated.approved:
            return done(
                CycleStatus.NO_ACTION,
                "KERNEL_REJECTED",
                f"vetoed on {', '.join(adjudicated.reject_reasons)}",
                **base,
            )

        # 5. Execution ------------------------------------------------------
        if dry_run or execution is None:
            steps.append("dry_run")
            return done(
                CycleStatus.SUCCESS,
                "DRY_RUN_APPROVED",
                "the Kernel approved and nothing was transmitted",
                **base,
            )

        steps.append("execute")
        result = execution.execute(proposal, adjudicated)

        return done(
            CycleStatus.SUCCESS if result.status is ExecutionStatus.FILLED else CycleStatus.ABORTED,
            str(result.status),
            result.detail,
            execution_status=str(result.status),
            **base,
        )

    except Exception as exc:
        # The scheduler must survive any cycle. ERR-006 alerts on three
        # consecutive failures; one is recorded and the next tick tries again.
        return done(CycleStatus.ERROR, type(exc).__name__, str(exc))


def _portfolio_context(context: KernelContext, limits: KernelLimits) -> PortfolioContext:
    """What the book looks like, in the shape the prompt expects."""
    deployable = context.account.nav * limits.max_deployed_pct
    utilization = (
        (context.total_reserve / deployable * Decimal("100")).quantize(Decimal("0.1"))
        if deployable > ZERO
        else ZERO
    )

    return PortfolioContext(
        nav=context.account.nav,
        open_policies=len(context.open_policies),
        reserve_utilization_pct=utilization,
        net_delta=context.portfolio_net_delta,
        net_vega=context.portfolio_net_vega,
        underlyings_held=tuple(sorted({p.underlying for p in context.open_policies})),
        mode=str(context.mode),
    )


def _with_candidates(context: KernelContext, proposals: tuple[Any, ...]) -> KernelContext:
    """Narrow SK-024's allowed set to this cycle's candidates."""
    from dataclasses import replace

    return replace(
        context,
        supplied_candidate_ids=frozenset(p.candidate_id for p in proposals),
    )

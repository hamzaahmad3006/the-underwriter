"""The management cycle — §12.2, FR-100.

Every open policy, every 15 minutes, evaluated against §15.4's precedence. The
Claims Desk decides; the Kernel authorises; the Execution Engine transmits.
Same three-step separation as an entry, for the same reason.

DEV-02 is why this cycle can run when the underwriting cycle cannot: blackout
windows bound entries, not exits. Refusing to close inside them would strand
risk the desk is obliged to remove, and FORCE_FLAT has a deadline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from underwriter.claims.desk import (
    DEFAULT_CLAIMS_POLICY,
    ClaimsPolicy,
    ClaimsVerdict,
    ManagedPosition,
    evaluate,
)
from underwriter.cycle.underwrite import CycleStatus, new_correlation_id
from underwriter.domain.proposal import Action, UnderwritingProposal
from underwriter.execution.engine import ExecutionEngine, ExecutionStatus
from underwriter.kernel import kernel
from underwriter.kernel.context import KernelContext
from underwriter.kernel.limits import DEFAULT_LIMITS, KernelLimits


@dataclass(frozen=True, slots=True)
class ExitAttempt:
    """One policy passing through the exit path this cycle."""

    policy_id: str
    policy_number: str
    claims: ClaimsVerdict
    verdict: str | None = None
    reject_reasons: tuple[str, ...] = ()
    execution_status: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ManagementReport:
    correlation_id: str
    status: CycleStatus
    evaluated: int = 0
    closed: int = 0
    held: int = 0
    escalations: tuple[str, ...] = ()
    attempts: tuple[ExitAttempt, ...] = field(default_factory=tuple)
    started_at: datetime = datetime(1970, 1, 1, tzinfo=UTC)
    finished_at: datetime | None = None
    detail: str = ""


def run_management_cycle(
    positions: tuple[ManagedPosition, ...],
    *,
    proposals_by_policy: dict[str, UnderwritingProposal],
    context: KernelContext,
    secret: str,
    execution: ExecutionEngine | None = None,
    as_of: date | None = None,
    claims_policy: ClaimsPolicy = DEFAULT_CLAIMS_POLICY,
    limits: KernelLimits = DEFAULT_LIMITS,
    dry_run: bool = False,
    correlation_id: str | None = None,
) -> ManagementReport:
    """Evaluate every open policy and act on the ones that should close.

    One policy failing does not stop the cycle. A stuck position is exactly
    when the others most need managing.
    """
    started = datetime.now(UTC)
    cid = correlation_id or new_correlation_id()
    today = as_of or started.date()

    attempts: list[ExitAttempt] = []
    escalations: list[str] = []
    closed = 0
    held = 0

    for position in positions:
        claims = evaluate(position, today, claims_policy)

        if claims.escalate:
            escalations.append(f"{position.policy_number}: {claims.detail}")

        if not claims.should_close:
            held += 1
            attempts.append(
                ExitAttempt(
                    policy_id=position.policy_id,
                    policy_number=position.policy_number,
                    claims=claims,
                    detail=claims.detail,
                )
            )
            continue

        attempt = _attempt_exit(
            position,
            claims,
            proposals_by_policy.get(position.policy_id),
            context=context,
            secret=secret,
            execution=execution,
            limits=limits,
            dry_run=dry_run,
        )
        attempts.append(attempt)
        if attempt.execution_status == str(ExecutionStatus.FILLED):
            closed += 1

    return ManagementReport(
        correlation_id=cid,
        status=CycleStatus.SUCCESS if positions else CycleStatus.NO_ACTION,
        evaluated=len(positions),
        closed=closed,
        held=held,
        escalations=tuple(escalations),
        attempts=tuple(attempts),
        started_at=started,
        finished_at=datetime.now(UTC),
        detail=f"{len(positions)} evaluated, {closed} closed, {held} held",
    )


def _attempt_exit(
    position: ManagedPosition,
    claims: ClaimsVerdict,
    proposal: UnderwritingProposal | None,
    *,
    context: KernelContext,
    secret: str,
    execution: ExecutionEngine | None,
    limits: KernelLimits,
    dry_run: bool,
) -> ExitAttempt:
    """FR-106: an exit goes through the Kernel like anything else."""
    if proposal is None:
        return ExitAttempt(
            policy_id=position.policy_id,
            policy_number=position.policy_number,
            claims=claims,
            detail="no stored proposal for this policy; cannot construct the closing order",
        )

    closing = replace(proposal, action=Action.CLOSE)
    exit_context = replace(context, supplied_candidate_ids=frozenset({closing.candidate_id}))

    verdict = kernel.evaluate(
        closing,
        requested_contracts=position.contracts,
        context=exit_context,
        secret=secret,
        limits=limits,
    )

    if not verdict.approved:
        # SK-000 exempts the capital rules, so a refused *close* means a safety
        # rule fired — a closed market, a halted system. Worth surfacing
        # loudly, because the position stays on the book either way.
        return ExitAttempt(
            policy_id=position.policy_id,
            policy_number=position.policy_number,
            claims=claims,
            verdict=str(verdict.verdict),
            reject_reasons=tuple(verdict.reject_reasons),
            detail=f"the Kernel refused the close: {', '.join(verdict.reject_reasons)}",
        )

    if dry_run or execution is None:
        return ExitAttempt(
            policy_id=position.policy_id,
            policy_number=position.policy_number,
            claims=claims,
            verdict=str(verdict.verdict),
            detail="approved; nothing transmitted (dry run)",
        )

    target: Decimal = claims.target_debit or Decimal("0.01")
    result = execution.execute(closing, verdict, target_debit=target)

    return ExitAttempt(
        policy_id=position.policy_id,
        policy_number=position.policy_number,
        claims=claims,
        verdict=str(verdict.verdict),
        execution_status=str(result.status),
        detail=result.detail,
    )


def summarise(report: ManagementReport) -> dict[str, Any]:
    """A compact shape for the dashboard and the scheduler log."""
    return {
        "correlation_id": report.correlation_id,
        "status": report.status,
        "evaluated": report.evaluated,
        "closed": report.closed,
        "held": report.held,
        "escalations": list(report.escalations),
        "detail": report.detail,
    }

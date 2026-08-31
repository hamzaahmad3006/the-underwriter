"""The Solvency Kernel.

> The AI Underwriter proposes. The Solvency Kernel decides whether execution is
> permitted. There is no third path.

Five properties this module is required to have, and where each one lives:

* **Deterministic** (SK-P1) — no LLM, no randomness in the decision, no network.
  The only clock reads are the explicit market-hours and TTL checks, and both
  take their `now` from the caller.
* **Fail closed** (SK-P2, FR-062) — every rule runs inside a guard, and so does
  the whole evaluation. An exception becomes a HARD failure, never a pass.
* **Complete** (SK-P3, FR-061) — all rules evaluate every time. The ledger has
  to show every reason a trade died, not just the first one found.
* **Non-bypassable** (SK-P4) — approval is a signed artifact minted here and
  verified in `verdict.authorize`. Nothing else can mint one.
* **Privileged closes** (SK-P6, SK-000) — a risk-reducing action is never
  blocked on capital grounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from underwriter.domain.money import CONTRACT_MULTIPLIER, ONE, ZERO
from underwriter.domain.proposal import Action, UnderwritingProposal
from underwriter.kernel.context import KernelContext
from underwriter.kernel.limits import DEFAULT_LIMITS, KernelLimits
from underwriter.kernel.rules import RULES, Evaluator, money
from underwriter.kernel.verdict import (
    FAIL_CLOSED,
    Decision,
    KernelVerdict,
    RuleResult,
    Severity,
    mint,
)


@dataclass(frozen=True, slots=True)
class Sizing:
    """How many contracts each constraint would permit, and why."""

    by_position_risk: int
    by_portfolio_room: int
    by_concentration: int
    by_deployed_capital: int
    by_assignment_cost: int

    @property
    def permitted(self) -> int:
        return max(
            0,
            min(
                self.by_position_risk,
                self.by_portfolio_room,
                self.by_concentration,
                self.by_deployed_capital,
                self.by_assignment_cost,
            ),
        )

    @property
    def binding_constraint(self) -> str:
        pairs = {
            "position_risk": self.by_position_risk,
            "portfolio_room": self.by_portfolio_room,
            "concentration": self.by_concentration,
            "deployed_capital": self.by_deployed_capital,
            "assignment_cost": self.by_assignment_cost,
        }
        return min(pairs, key=lambda k: pairs[k])


def _floor_div(numerator: Decimal, denominator: Decimal) -> int:
    """Whole contracts only, always rounding down. Zero if the room is negative."""
    if denominator <= ZERO:
        return 0
    if numerator <= ZERO:
        return 0
    return int((numerator / denominator).to_integral_value(rounding=ROUND_FLOOR))


def compute_sizing(
    proposal: UnderwritingProposal, context: KernelContext, limits: KernelLimits
) -> Sizing:
    """§15.5, plus the two constraints the formula block leaves implicit.

    The SRS lists position risk, portfolio room and concentration. SK-001
    (deployed capital) and SK-012 (assignment cost) bound size just as directly
    — a proposal that passes them for one contract can still exceed them at
    five — so they are computed here too. Adding them can only ever reduce
    size, never permit more.
    """
    account = context.account
    max_loss = proposal.max_loss

    by_position_risk = _floor_div(account.nav * limits.max_position_loss_pct, max_loss)

    portfolio_room = account.nav * limits.max_portfolio_risk_pct - context.portfolio_max_loss
    by_portfolio_room = _floor_div(portfolio_room, max_loss)

    deployable = account.nav * limits.max_deployed_pct
    concentration_room = deployable * limits.max_underlying_concentration - context.reserve_for(
        proposal.underlying
    )
    by_concentration = _floor_div(concentration_room, max_loss)

    deployed_room = deployable - context.total_reserve
    by_deployed_capital = _floor_div(deployed_room, max_loss)

    assignment_room = account.buying_power - context.total_assignment_cost
    by_assignment_cost = _floor_div(assignment_room, proposal.short_strike * CONTRACT_MULTIPLIER)

    return Sizing(
        by_position_risk=by_position_risk,
        by_portfolio_room=by_portfolio_room,
        by_concentration=by_concentration,
        by_deployed_capital=by_deployed_capital,
        by_assignment_cost=by_assignment_cost,
    )


def _guarded(
    spec_id: str,
    spec_name: str,
    severity: Severity,
    evaluate: Evaluator,
    proposal: UnderwritingProposal,
    context: KernelContext,
    limits: KernelLimits,
) -> RuleResult:
    """Run one rule so that any exception becomes a HARD failure (TEST-041).

    A rule that raises is a rule whose answer we do not know, and "we do not
    know" is not permission.
    """
    try:
        result = evaluate(proposal, context, limits)
    except Exception as exc:
        return RuleResult(
            rule_id=spec_id,
            name=spec_name,
            passed=False,
            severity=Severity.HARD,
            observed=f"{type(exc).__name__}: {exc}",
            limit="rule must evaluate without raising",
            message="Rule evaluation raised; failing closed (SK-P2).",
            reason_code=FAIL_CLOSED,
        )
    if not isinstance(result, RuleResult):
        return RuleResult(
            rule_id=spec_id,
            name=spec_name,
            passed=False,
            severity=Severity.HARD,
            observed=f"returned {type(result).__name__}",
            limit="rule must return a RuleResult",
            message="Rule returned a non-verdict; failing closed (SK-P2).",
            reason_code=FAIL_CLOSED,
        )
    return result


def evaluate(
    proposal: UnderwritingProposal,
    *,
    requested_contracts: int,
    context: KernelContext,
    secret: str,
    limits: KernelLimits = DEFAULT_LIMITS,
) -> KernelVerdict:
    """Adjudicate one proposal. Always returns a verdict; never raises.

    `requested_contracts` is the LLM's advisory number (FR-046). It can only
    ever reduce the outcome: the Kernel takes the minimum of what was asked and
    what is permitted, so a model asking for 500 contracts gets the same answer
    as one asking for the permitted size.
    """
    try:
        return _evaluate(
            proposal,
            requested_contracts=requested_contracts,
            context=context,
            secret=secret,
            limits=limits,
        )
    except Exception as exc:  # FR-062: nothing escapes as an approval
        failure = RuleResult(
            rule_id="SK-999",
            name="kernel_integrity",
            passed=False,
            severity=Severity.HARD,
            observed=f"{type(exc).__name__}: {exc}",
            limit="kernel must complete evaluation",
            message="Kernel evaluation failed; refusing by default (SK-P2).",
            reason_code=FAIL_CLOSED,
        )
        return KernelVerdict(
            verdict_id="vd_failclosed",
            proposal_hash=_safe_hash(proposal),
            verdict=Decision.REJECT,
            approved_contracts=0,
            rules=(failure,),
            reject_reasons=(FAIL_CLOSED,),
            nonce="",
            issued_at=context.now,
            expires_at=context.now,
            signature=None,
        )


def _safe_hash(proposal: UnderwritingProposal) -> str:
    """Hash the proposal for the ledger, even if hashing is what broke."""
    try:
        return proposal.proposal_hash
    except Exception:
        return "unhashable"


def _evaluate(
    proposal: UnderwritingProposal,
    *,
    requested_contracts: int,
    context: KernelContext,
    secret: str,
    limits: KernelLimits,
) -> KernelVerdict:
    results: list[RuleResult] = [
        _guarded(spec.rule_id, spec.name, spec.severity, spec.evaluate, proposal, context, limits)
        for spec in RULES
    ]

    # SOFT rules never reject; each failure halves the permitted size (§14.2).
    soft_multiplier = ONE
    for result in results:
        if result.is_soft_failure:
            soft_multiplier *= limits.soft_factor

    if proposal.action is Action.CLOSE:
        # A close is bounded by the position being closed, not by capital.
        permitted = max(0, requested_contracts)
        sizing_note = "action=CLOSE: capital sizing not applied (SK-000)"
    else:
        sizing = compute_sizing(proposal, context, limits)
        permitted = sizing.permitted
        sizing_note = (
            f"permitted={sizing.permitted} "
            f"(binding: {sizing.binding_constraint}), "
            f"requested={requested_contracts}, soft_multiplier={soft_multiplier}"
        )

    raw = Decimal(min(max(requested_contracts, 0), permitted))
    approved = int(
        (raw * soft_multiplier * limits.calibration_multiplier).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )

    results.append(
        RuleResult(
            rule_id="SK-020",
            name="size_floor",
            passed=approved >= 1,
            severity=Severity.HARD,
            observed=f"approved_contracts={approved}",
            limit="minimum 1 contract",
            message=f"Size after all reductions. {sizing_note}",
            reason_code="" if approved >= 1 else "SIZE_FLOOR",
        )
    )

    hard_failures = tuple(r for r in results if r.is_hard_failure)
    decision = Decision.REJECT if hard_failures else Decision.APPROVE
    reject_reasons = tuple(r.reason_code or r.rule_id for r in hard_failures)

    return mint(
        proposal_hash=proposal.proposal_hash,
        verdict=decision,
        approved_contracts=approved if decision is Decision.APPROVE else 0,
        rules=tuple(results),
        reject_reasons=reject_reasons,
        issued_at=context.now,
        ttl_sec=limits.verdict_ttl_sec,
        secret=secret,
    )


def explain(verdict: KernelVerdict) -> str:
    """One-line-per-rule rendering, for the Veto Feed and the CLI (§21.5)."""
    lines = [
        f"{verdict.verdict} {verdict.approved_contracts} contract(s)  "
        f"[{verdict.verdict_id}] expires {verdict.expires_at.isoformat()}"
    ]
    for rule in verdict.rules:
        mark = "PASS" if rule.passed else ("FAIL" if rule.severity is Severity.HARD else "SOFT")
        lines.append(
            f"  {mark:4}  {rule.rule_id}  {rule.name:26}  {rule.observed}  |  {rule.limit}"
        )
    return "\n".join(lines)


__all__ = [
    "Sizing",
    "compute_sizing",
    "evaluate",
    "explain",
    "money",
]

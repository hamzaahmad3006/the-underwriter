"""Operator-initiated actions — API-022, API-052.

These are the two things a human can ask the desk to do, and both are
deliberately unremarkable: they enter the same pipeline as everything else.

SEC-012 is the point. An operator asking to close a policy produces a proposal,
the Kernel adjudicates it against the same 25 rules, and the Execution Engine
demands the same signed verdict. There is no privileged path, no force flag and
no override — TD-11 records that the *absence* of one is the demo.

So a close the Kernel refuses stays refused, whoever asked. The endpoint
returns 403 with the failing rules rather than a way around them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from underwriter.audit.ledger import Actor, append
from underwriter.claims.desk import DEFAULT_CLAIMS_POLICY, evaluate
from underwriter.cycle import bootstrap
from underwriter.cycle.reconcile import run_reconcile_cycle
from underwriter.db import session_scope
from underwriter.db.models import Policy
from underwriter.kernel import kernel
from underwriter.middleware.error_handler import EndpointNotReadyError

CLOSABLE = ("OPEN", "CLOSING", "LEG_RISK")


def request_close(policy_id: str, reason: str, *, actor: str = "OPERATOR") -> dict[str, Any]:
    """API-022 — ask the Kernel to authorise closing a policy.

    Requests a close; never performs one. The request is recorded before it is
    adjudicated, so an operator asking for something the Kernel then refuses is
    in the ledger either way — the asking is the interesting part.
    """
    now = datetime.now(UTC)

    with session_scope() as session:
        policy = session.get(Policy, policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail=f"no policy with id {policy_id}")

        if policy.status not in CLOSABLE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"policy is {policy.status}; only {', '.join(CLOSABLE)} can be closed",
            )

        append(
            session,
            actor=Actor.OPERATOR,
            action="CLOSE_REQUESTED",
            entity_type="policy",
            entity_id=policy_id,
            after={"reason": reason, "policy_number": policy.policy_number},
            correlation_id=policy.correlation_id,
        )
        policy_number = policy.policy_number
        contracts = policy.contracts

    # The same reconstruction the management cycle uses. One path, not two.
    positions, proposals = bootstrap.open_positions()
    proposal = proposals.get(policy_id)
    position = next((p for p in positions if p.policy_id == policy_id), None)

    if proposal is None or position is None:
        raise EndpointNotReadyError(
            f"Closing {policy_number}",
            "the policy's legs could not be reconstructed into a closing order",
        )

    context = replace(
        bootstrap.build_kernel_context(now=now),
        supplied_candidate_ids=frozenset({proposal.candidate_id}),
    )
    verdict = kernel.evaluate(
        proposal,
        requested_contracts=contracts,
        context=context,
        secret=bootstrap.signing_secret(),
    )

    with session_scope() as session:
        from underwriter.cycle.recorder import CycleRecorder

        recorder = CycleRecorder(session, f"operator:{policy_id}")
        recorder.verdict(verdict, candidate_row_id=None, decision_row_id=None, action_type="EXIT")

    claims = evaluate(position, now.date(), DEFAULT_CLAIMS_POLICY)
    body: dict[str, Any] = {
        "as_of": now.isoformat(),
        "policy_number": policy_number,
        "requested_by": actor,
        "reason": reason,
        "kernel_decision_id": verdict.verdict_id,
        "verdict": str(verdict.verdict),
        "approved_contracts": verdict.approved_contracts,
        "reject_reasons": list(verdict.reject_reasons),
        "claims_view": claims.detail,
        # This endpoint has no execution engine behind it. The management cycle
        # transmits; this asks. A control that could both ask and act would be
        # the second path SEC-012 exists to prevent.
        "executed": False,
    }

    if not verdict.approved:
        # 403 with the failing rules, never a way around them (TD-11).
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=body)

    body["detail"] = (
        "The Kernel authorised the close. The next management cycle transmits it; "
        "this endpoint does not."
    )
    return body


def force_reconcile(*, actor: str = "OPERATOR") -> dict[str, Any]:
    """API-052 — run the reconciliation cycle now.

    Read-only against the broker and safe to invoke at any time, which is why
    it is the one operator control that acts immediately rather than asking.
    Finding a divergence sooner is strictly better than finding it on schedule.
    """
    broker = bootstrap.build_broker()
    if broker is None:
        raise EndpointNotReadyError(
            "Forced reconciliation",
            "no Alpaca trading credentials are configured, so the broker cannot be read",
        )

    report = run_reconcile_cycle(broker)

    if report.forced_manage_only:
        bootstrap.force_manage_only(report.detail)

    with session_scope() as session:
        append(
            session,
            actor=Actor.OPERATOR,
            action="RECONCILE_REQUESTED",
            after={"orphans": list(report.orphans), "invariant": report.invariant_holds},
            correlation_id=report.correlation_id,
        )

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "requested_by": actor,
        "correlation_id": report.correlation_id,
        "status": str(report.status),
        "broker_positions": report.broker_positions,
        "book_policies": report.book_policies,
        "orphans": list(report.orphans),
        "missing": list(report.missing),
        "reserve_invariant_holds": report.invariant_holds,
        "reserve_invariant_detail": report.invariant_detail,
        "forced_manage_only": report.forced_manage_only,
        "equity": str(report.equity) if report.equity is not None else None,
        "detail": report.detail,
    }

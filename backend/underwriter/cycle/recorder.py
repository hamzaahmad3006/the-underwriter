"""Writing a cycle down — FR-008, FR-023, FR-043, FR-065, DB-017.

This is what turns a pipeline into a book. Without it the desk decides things
and forgets them, the dashboard shows zeros forever, and NFR-008's "every
executed order traceable to a stored verdict" has nothing to trace to.

Two rules hold throughout:

* **The audit record and the change it describes commit together.** They share
  one session and one transaction, so the ledger can never claim something the
  book did not do, or miss something it did.
* **Recording happens before the thing it authorises.** FR-065 requires the
  Kernel to persist a verdict *before* returning it, and F-11 makes recording
  a precondition for trading — the system never trades unrecorded.

Every write also appends to the hash chain, so a cycle is reconstructible from
the ledger alone and any later edit to it is detectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from underwriter.actuary.engine import ActuaryResult
from underwriter.agent.underwriter import UnderwritingOutcome
from underwriter.audit.ledger import Actor, append
from underwriter.data.snapshot import SnapshotResult
from underwriter.db.models import (
    Candidate,
    KernelDecision,
    MarketSnapshotRow,
    Order,
    Policy,
    PolicyLeg,
    Reserve,
    RiskCheck,
    RiskEvent,
    UnderwritingDecision,
)
from underwriter.domain.money import ZERO
from underwriter.domain.proposal import UnderwritingProposal
from underwriter.execution.engine import ExecutionResult, ExecutionStatus
from underwriter.kernel.verdict import KernelVerdict


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def next_policy_number(session: Session, *, now: datetime | None = None) -> str:
    """`UW-2026-0007` — human-friendly and unique (DB-009).

    Sequential per year rather than a UUID because a person reads this one:
    it goes in alerts, in the demo, and in whatever a judge writes down.
    """
    year = (now or datetime.now(UTC)).year
    prefix = f"UW-{year}-"
    count = session.execute(
        select(func.count()).select_from(Policy).where(Policy.policy_number.like(f"{prefix}%"))
    ).scalar_one()
    return f"{prefix}{count + 1:04d}"


@dataclass(slots=True)
class CycleRecorder:
    """Records one cycle, step by step, into one session.

    The caller owns the transaction. Each method returns the row id the next
    step needs, so the chain of foreign keys is built as the cycle runs rather
    than reconstructed afterwards from timestamps.
    """

    session: Session
    correlation_id: str

    # -- market data ------------------------------------------------------

    def snapshot(self, result: SnapshotResult) -> str | None:
        """DB-004, FR-008 — the exact inputs, enough to replay the Actuary."""
        if result.snapshot is None:
            return None

        snap = result.snapshot
        existing = self.session.execute(
            select(MarketSnapshotRow).where(MarketSnapshotRow.snapshot_hash == snap.snapshot_hash)
        ).scalar_one_or_none()
        if existing is not None:
            # Same inputs, same hash: the snapshot is already recorded and
            # writing it twice would break the UNIQUE constraint for no gain.
            return existing.id

        underlying = next(iter(snap.underlying_prices), "")
        volatility = result.volatility.get(underlying)

        row = MarketSnapshotRow(
            correlation_id=self.correlation_id,
            underlying=underlying,
            underlying_price=snap.underlying_prices.get(underlying),
            iv_rank=volatility.iv_rank if volatility else None,
            realized_vol=volatility.realized_vol if volatility else None,
            chain_json={
                "quotes": [
                    {
                        "symbol": q.symbol,
                        "underlying": q.underlying,
                        "strike": str(q.strike),
                        "expiry": q.expiry.isoformat(),
                        "bid": str(q.bid),
                        "ask": str(q.ask),
                        "bid_size": q.bid_size,
                        "ask_size": q.ask_size,
                        "delta": _str(q.delta),
                        "vega": _str(q.vega),
                        "iv": _str(q.implied_volatility),
                        "open_interest": q.open_interest,
                        "fetched_at": q.fetched_at.isoformat(),
                    }
                    for q in snap.quotes
                ],
                "measure": str(volatility.measure) if volatility else None,
            },
            snapshot_hash=snap.snapshot_hash,
            source="rest",
            fetched_at=snap.as_of,
        )
        self.session.add(row)
        self.session.flush()

        append(
            self.session,
            actor=Actor.SCHEDULER,
            action="SNAPSHOT_TAKEN",
            entity_type="market_snapshot",
            entity_id=row.id,
            after={"snapshot_hash": snap.snapshot_hash, "quotes": len(snap.quotes)},
            correlation_id=self.correlation_id,
        )
        return row.id

    # -- actuary ----------------------------------------------------------

    def candidates(self, priced: ActuaryResult, snapshot_id: str | None) -> dict[str, str]:
        """DB-005, FR-023 — every candidate, accepted and discarded alike.

        The discards are the half that explains an empty book, so they are
        stored with the same care as the survivors.
        """
        ids: dict[str, str] = {}

        for proposal in priced.proposals:
            # A quiet market can produce an identical snapshot, and therefore
            # identical candidates, two cycles running. `proposal_hash` is
            # UNIQUE, so re-inserting would roll back the whole phase and lose
            # the cycle's audit records along with it.
            existing = self.session.execute(
                select(Candidate).where(Candidate.proposal_hash == proposal.proposal_hash)
            ).scalar_one_or_none()
            if existing is not None:
                ids[proposal.candidate_id] = existing.id
                continue

            row = Candidate(
                correlation_id=self.correlation_id,
                snapshot_id=snapshot_id,
                underlying=proposal.underlying,
                structure=str(proposal.structure),
                short_symbol=proposal.legs[0].symbol if proposal.legs else None,
                long_symbol=proposal.legs[1].symbol if len(proposal.legs) > 1 else None,
                short_strike=proposal.short_strike,
                long_strike=proposal.long_strike,
                width=proposal.width,
                expiration=proposal.expiry.isoformat(),
                dte=proposal.dte,
                net_credit=proposal.net_credit,
                max_profit=proposal.max_profit,
                max_loss=proposal.max_loss,
                capital_reserve=proposal.capital_reserve,
                breakeven=proposal.breakeven,
                short_delta=proposal.short_delta,
                p_profit_proxy=proposal.p_profit_proxy,
                expected_value=proposal.expected_value,
                edge_ratio=proposal.edge_ratio,
                liquidity_score=proposal.liquidity_score,
                bid_ask_pct=proposal.max_leg_spread_pct,
                accepted=1,
                proposal_hash=proposal.proposal_hash,
            )
            self.session.add(row)
            self.session.flush()
            ids[proposal.candidate_id] = row.id

        for index, discard in enumerate(priced.discards):
            # Namespaced to the cycle, so repeated discards never collide.
            self.session.add(
                Candidate(
                    correlation_id=self.correlation_id,
                    snapshot_id=snapshot_id,
                    underlying="",
                    structure="PUT_CREDIT_SPREAD",
                    accepted=0,
                    rejection_reason=str(discard.reason),
                    # Discards have no proposal to hash, but the column is
                    # UNIQUE, so the id is namespaced to this cycle.
                    proposal_hash=f"discard:{self.correlation_id}:{index}",
                )
            )

        append(
            self.session,
            actor=Actor.ACTUARY,
            action="CANDIDATES_PRICED",
            entity_type="market_snapshot",
            entity_id=snapshot_id,
            after={"priced": len(priced.proposals), "discarded": len(priced.discards)},
            correlation_id=self.correlation_id,
        )
        return ids

    # -- the model --------------------------------------------------------

    def decision(self, outcome: UnderwritingOutcome, candidate_row_id: str | None) -> str:
        """DB-006, FR-043 — everything needed to reproduce the call."""
        decision = outcome.decision
        action = decision.action if decision else "DECLINE"

        row = UnderwritingDecision(
            correlation_id=self.correlation_id,
            candidate_id=candidate_row_id,
            action=action,
            confidence=decision.confidence_decimal if decision else None,
            requested_contracts=decision.contracts if decision else None,
            rationale=decision.rationale if decision else outcome.detail,
            identified_risks_json=list(decision.identified_risks or []) if decision else [],
            declined_reason=decision.declined_reason if decision else outcome.detail,
            model=outcome.model,
            model_version=outcome.model_version,
            temperature=Decimal(str(outcome.temperature)) if outcome.temperature else None,
            prompt_sha256=outcome.prompt_sha256,
            raw_response=outcome.raw_response,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            latency_ms=outcome.latency_ms,
            schema_valid=0 if outcome.abort_reason else 1,
            retry_count=outcome.retry_count,
        )
        self.session.add(row)
        self.session.flush()

        append(
            self.session,
            actor=Actor.UNDERWRITER,
            action=f"DECISION_{action}",
            entity_type="underwriting_decision",
            entity_id=row.id,
            after={
                "action": action,
                "candidate_id": decision.candidate_id if decision else None,
                "confidence": _str(decision.confidence_decimal) if decision else None,
                "prompt_sha256": outcome.prompt_sha256,
                "model_version": outcome.model_version,
            },
            correlation_id=self.correlation_id,
        )
        return row.id

    # -- the kernel -------------------------------------------------------

    def verdict(
        self,
        verdict: KernelVerdict,
        *,
        candidate_row_id: str | None,
        decision_row_id: str | None,
        action_type: str = "ENTRY",
    ) -> str:
        """DB-007 and DB-008, FR-065 — persisted *before* it is acted on.

        Every rule is stored, passes included, because the ledger has to show
        every reason a trade died rather than only the first one found.
        """
        row = KernelDecision(
            correlation_id=self.correlation_id,
            candidate_id=candidate_row_id,
            decision_id=decision_row_id,
            proposal_hash=verdict.proposal_hash,
            verdict=str(verdict.verdict),
            approved_contracts=verdict.approved_contracts,
            reject_reasons_json=list(verdict.reject_reasons),
            nonce=verdict.nonce or f"unsigned:{verdict.verdict_id}",
            issued_at=verdict.issued_at,
            expires_at=verdict.expires_at,
            signature=verdict.signature,
            action_type=action_type,
        )
        self.session.add(row)
        self.session.flush()

        for rule in verdict.rules:
            self.session.add(
                RiskCheck(
                    kernel_decision_id=row.id,
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    passed=1 if rule.passed else 0,
                    severity=str(rule.severity),
                    observed=rule.observed,
                    limit_value=rule.limit,
                    message=rule.message,
                )
            )

        append(
            self.session,
            actor=Actor.KERNEL,
            action=f"VERDICT_{verdict.verdict}",
            entity_type="kernel_decision",
            entity_id=row.id,
            after={
                "verdict": str(verdict.verdict),
                "approved_contracts": verdict.approved_contracts,
                "reject_reasons": list(verdict.reject_reasons),
                "rules_evaluated": len(verdict.rules),
                # Whether one was minted, never the signature itself.
                "signed": verdict.signature is not None,
            },
            correlation_id=self.correlation_id,
        )
        return row.id

    # -- execution --------------------------------------------------------

    def policy(
        self,
        proposal: UnderwritingProposal,
        *,
        contracts: int,
        candidate_row_id: str | None,
        verdict_row_id: str,
        confidence: Decimal | None,
        status: str = "PENDING",
    ) -> str:
        """DB-009, DB-010 and DB-015 — the policy, its legs, and its reserve.

        SK-002 says a policy is reserved at exactly its max loss, so the
        reserve is written here rather than later: a policy that exists without
        one would break DB-INV-1 on the next reconcile.
        """
        max_loss = proposal.max_loss * Decimal(contracts)
        number = next_policy_number(self.session)

        row = Policy(
            policy_number=number,
            correlation_id=self.correlation_id,
            candidate_id=candidate_row_id,
            kernel_decision_id=verdict_row_id,
            underlying=proposal.underlying,
            structure=str(proposal.structure),
            contracts=contracts,
            opening_credit=proposal.net_credit,
            max_profit=proposal.max_profit * Decimal(contracts),
            max_loss=max_loss,
            capital_reserve=max_loss,
            expiration=proposal.expiry.isoformat(),
            status=status,
            predicted_confidence=confidence,  # FR-044: before the outcome
        )
        self.session.add(row)
        self.session.flush()

        for leg in proposal.legs:
            self.session.add(
                PolicyLeg(
                    policy_id=row.id,
                    option_symbol=leg.symbol,
                    side=str(leg.side),
                    position_intent="sell_to_open" if str(leg.side) == "SELL" else "buy_to_open",
                    ratio_qty=leg.ratio_qty,
                    strike=leg.strike,
                    expiration=leg.expiry.isoformat(),
                    option_type=str(leg.right),
                    open_delta=proposal.short_delta if str(leg.side) == "SELL" else None,
                )
            )

        self.session.add(Reserve(policy_id=row.id, amount=max_loss, status="HELD"))

        append(
            self.session,
            actor=Actor.EXECUTION,
            action="POLICY_WRITTEN",
            entity_type="policy",
            entity_id=row.id,
            after={
                "policy_number": number,
                "underlying": proposal.underlying,
                "contracts": contracts,
                "max_loss": str(max_loss),
                "reserved": str(max_loss),
            },
            correlation_id=self.correlation_id,
        )
        return row.id

    def order(self, result: ExecutionResult, *, policy_id: str, verdict_row_id: str) -> str:
        """DB-011 — the order, with the verdict that authorised it.

        `kernel_decision_id` is NOT NULL, so this method structurally cannot
        record an order without one.
        """
        row = Order(
            policy_id=policy_id,
            alpaca_order_id=result.broker_order_id,
            client_order_id=result.client_order_id,
            kernel_decision_id=verdict_row_id,
            intent="ENTRY",
            order_class="mleg",
            limit_price=(
                Decimal(str(result.request.get("limit_price"))) if result.request else None
            ),
            status=str(result.status),
            filled_qty=result.contracts_filled,
            filled_avg_price=result.filled_avg_price,
            request_json=result.request,
            response_json=result.response,
            attempt=result.attempts,
            terminal=1,
            error_message=result.detail,
            submitted_at=result.as_of,
        )
        self.session.add(row)
        self.session.flush()

        append(
            self.session,
            actor=Actor.EXECUTION,
            action=f"ORDER_{result.status}",
            entity_type="order",
            entity_id=row.id,
            after={
                "client_order_id": result.client_order_id,
                "status": str(result.status),
                "filled": str(result.contracts_filled),
                "kernel_decision_id": verdict_row_id,
            },
            correlation_id=self.correlation_id,
        )

        if result.status is ExecutionStatus.PARTIAL:
            self.risk_event(
                "LEG_RISK",
                "CRITICAL",
                policy_id=policy_id,
                detail={"detail": result.detail, "filled": str(result.contracts_filled)},
            )

        return row.id

    # -- anything that should wake the operator ---------------------------

    def risk_event(
        self,
        event_type: str,
        severity: str,
        *,
        policy_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        """DB-016, and an audit entry alongside it."""
        row = RiskEvent(
            event_type=event_type,
            severity=severity,
            policy_id=policy_id,
            detail_json=detail or {},
        )
        self.session.add(row)
        self.session.flush()

        append(
            self.session,
            actor=Actor.CLAIMS,
            action=f"RISK_EVENT_{event_type}",
            entity_type="risk_event",
            entity_id=row.id,
            after={"severity": severity, **(detail or {})},
            correlation_id=self.correlation_id,
        )
        return row.id

    def settle(
        self,
        policy_id: str,
        *,
        closing_debit: Decimal,
        realized: Decimal,
        reason: str,
    ) -> None:
        """FR-107 — settle the policy and release its reserve.

        The reserve release is not optional bookkeeping: a settled policy whose
        reserve is still HELD breaks DB-INV-1 on the next reconcile, and the
        book would report capital tied up against a position that is gone.
        """
        policy = self.session.get(Policy, policy_id)
        if policy is None:
            return

        before = {"status": policy.status, "realized_pnl": _str(policy.realized_pnl)}

        policy.status = "SETTLED"
        policy.closed_at = datetime.now(UTC)
        policy.closing_debit = closing_debit
        policy.realized_pnl = realized
        policy.settlement_reason = reason
        policy.outcome_win = 1 if realized > ZERO else 0

        for reserve in (
            self.session.execute(
                select(Reserve).where(Reserve.policy_id == policy_id, Reserve.status == "HELD")
            )
            .scalars()
            .all()
        ):
            reserve.status = "RELEASED"
            reserve.released_at = datetime.now(UTC)

        append(
            self.session,
            actor=Actor.CLAIMS,
            action="POLICY_SETTLED",
            entity_type="policy",
            entity_id=policy_id,
            before=before,
            after={
                "status": "SETTLED",
                "realized_pnl": str(realized),
                "settlement_reason": reason,
                "reserve": "RELEASED",
            },
            correlation_id=self.correlation_id,
        )

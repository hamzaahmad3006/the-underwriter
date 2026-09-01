"""The cycle actually writes the book down — FR-008, FR-043, FR-065, DB-INV-1.

This is the file that separates "components that work" from "a system that
runs". Without it the desk decides things and forgets them, the dashboard shows
zeros forever, and NFR-008 has nothing to trace an order to.

Every test here asserts against rows, not return values. A cycle report saying
a verdict was minted proves nothing if no verdict row exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.conftest import SECRET, make_context
from tests.test_agent import CONTEXT as PORTFOLIO
from tests.test_agent import FakeLLM, decision_json, write_json
from tests.test_data_layer import FakeSource, raw
from tests.test_execution import FakeBroker
from underwriter.actuary.engine import price_put_credit_spreads
from underwriter.agent.underwriter import AIUnderwriter
from underwriter.audit.ledger import verify_chain
from underwriter.cycle.reconcile import run_reconcile_cycle
from underwriter.cycle.recorder import CycleRecorder, next_policy_number
from underwriter.cycle.underwrite import CycleStatus, run_underwriting_cycle
from underwriter.data.snapshot import SnapshotConfig, build_snapshot
from underwriter.db import create_all, reset_engine, session_scope
from underwriter.db.invariants import check_reserve_invariant
from underwriter.db.models import (
    AuditLog,
    Candidate,
    KernelDecision,
    MarketSnapshotRow,
    Order,
    PnlRecord,
    Policy,
    PositionSnapshot,
    Reserve,
    RiskCheck,
    RiskEvent,
    UnderwritingDecision,
)
from underwriter.execution.engine import ExecutionEngine, ExecutionStatus
from underwriter.execution.ports import BrokerPosition, OrderState
from underwriter.execution.ratelimit import TokenBucket
from underwriter.kernel import kernel

SOLO = SnapshotConfig(universe=("SPY",))


@pytest.fixture(autouse=True)
def db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cycle.db'}")
    reset_engine()
    create_all()
    yield
    reset_engine()


def chain() -> FakeSource:
    return FakeSource(
        contracts=[
            raw(symbol="SPY_550", strike="550", bid="2.00", ask="2.10", delta="-0.20"),
            raw(symbol="SPY_548", strike="548", bid="1.40", ask="1.50", delta="-0.15"),
        ]
    )


def candidate_id() -> str:
    snapshot = build_snapshot(chain(), config=SOLO).snapshot
    assert snapshot is not None
    return price_put_credit_spreads(snapshot).proposals[0].candidate_id


def run(llm: FakeLLM, **kwargs: object):  # type: ignore[no-untyped-def]
    return run_underwriting_cycle(
        source=kwargs.pop("source", None) or chain(),  # type: ignore[arg-type]
        agent=AIUnderwriter(llm),
        context=kwargs.pop("context", None) or make_context(),  # type: ignore[arg-type]
        secret=SECRET,
        snapshot_config=SOLO,
        persist=True,
        dry_run=kwargs.pop("dry_run", True),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def count(model) -> int:  # type: ignore[no-untyped-def]
    with session_scope() as session:
        return len(session.execute(select(model)).scalars().all())


# ---------------------------------------------------------------------------
# The cycle writes what it did
# ---------------------------------------------------------------------------


def test_a_full_cycle_records_every_stage() -> None:
    """One correlation id, one story: snapshot, candidates, decision, verdict."""
    report = run(FakeLLM(write_json(candidate_id())))
    assert report.status is CycleStatus.SUCCESS

    with session_scope() as session:
        snapshot = session.execute(select(MarketSnapshotRow)).scalar_one()
        decision = session.execute(select(UnderwritingDecision)).scalar_one()
        verdict = session.execute(select(KernelDecision)).scalar_one()

        assert snapshot.correlation_id == report.correlation_id
        assert decision.correlation_id == report.correlation_id
        assert verdict.correlation_id == report.correlation_id

        assert snapshot.snapshot_hash == report.snapshot_hash
        assert decision.action == "WRITE"
        assert verdict.verdict == "APPROVE"


def test_every_rule_is_stored_not_only_the_failures() -> None:
    """FR-061: the ledger shows every reason, including the ones that passed."""
    run(FakeLLM(write_json(candidate_id())))

    with session_scope() as session:
        checks = session.execute(select(RiskCheck)).scalars().all()
        assert len(checks) == 26  # 25 rules plus SK-020 after sizing
        assert any(check.passed == 1 for check in checks)
        assert all(check.observed for check in checks)


def test_discarded_candidates_are_kept_with_their_reasons() -> None:
    """FR-023. The discards are the half that explains an empty book."""
    source = FakeSource(contracts=[raw(symbol="A", delta=None), raw(symbol="B", strike="548")])
    report = run(FakeLLM(), source=source)

    assert report.status is CycleStatus.NO_ACTION

    with session_scope() as session:
        rows = session.execute(select(Candidate)).scalars().all()
        assert rows
        assert all(row.accepted == 0 for row in rows)
        assert any(row.rejection_reason == "MISSING_GREEKS" for row in rows)


def test_a_declined_cycle_still_records_the_decision() -> None:
    """A decline is a decision. Forgetting it would hide the model's judgment."""
    report = run(FakeLLM(decision_json()))
    assert report.status is CycleStatus.NO_ACTION

    with session_scope() as session:
        decision = session.execute(select(UnderwritingDecision)).scalar_one()
        assert decision.action == "DECLINE"
        assert decision.declined_reason
        assert session.execute(select(KernelDecision)).scalars().all() == []


def test_an_aborted_decision_is_recorded_too() -> None:
    """The calls worth studying are the ones that went wrong."""
    run(FakeLLM(write_json("cand_hallucinated"), write_json("cand_still_fake")))

    with session_scope() as session:
        decision = session.execute(select(UnderwritingDecision)).scalar_one()
        assert decision.schema_valid == 0
        assert decision.retry_count == 1


def test_provenance_is_stored_with_every_decision() -> None:
    """FR-043: a decision nobody can reproduce is a decision nobody can audit."""
    run(FakeLLM(write_json(candidate_id())))

    with session_scope() as session:
        decision = session.execute(select(UnderwritingDecision)).scalar_one()
        assert decision.model_version == "openai/gpt-oss-120b"
        assert decision.prompt_sha256 and len(decision.prompt_sha256) == 64
        assert decision.prompt_tokens == 210
        assert decision.latency_ms == 640
        assert decision.raw_response


def test_the_verdict_is_recorded_before_anything_acts_on_it() -> None:
    """FR-065 and F-11: recording is a precondition, not bookkeeping."""
    report = run(FakeLLM(write_json(candidate_id())))
    assert report.steps.index("record_verdict") < report.steps.index("dry_run")


def test_a_rejected_verdict_is_stored_with_its_reasons() -> None:
    """The Veto Feed needs the rejections most of all.

    The market is open for the data layer here but closed for the Kernel, so
    the cycle runs the whole way and dies at adjudication — which is exactly
    the path a veto takes.
    """
    report = run(FakeLLM(write_json(candidate_id())), context=make_context(market_open=False))

    assert report.status is CycleStatus.NO_ACTION
    assert report.outcome == "KERNEL_REJECTED"

    with session_scope() as session:
        verdict = session.execute(select(KernelDecision)).scalar_one()
        assert verdict.verdict == "REJECT"
        assert "MARKET_CLOSED" in (verdict.reject_reasons_json or [])
        assert verdict.signature is None  # a rejection is never signed
        assert len(session.execute(select(RiskCheck)).scalars().all()) == 26


# ---------------------------------------------------------------------------
# The audit chain covers the cycle
# ---------------------------------------------------------------------------


def test_the_cycle_appends_to_the_hash_chain() -> None:
    run(FakeLLM(write_json(candidate_id())))

    with session_scope() as session:
        records = session.execute(select(AuditLog).order_by(AuditLog.seq)).scalars().all()
        actions = [row.action for row in records]

        assert "SNAPSHOT_TAKEN" in actions
        assert "CANDIDATES_PRICED" in actions
        assert "DECISION_WRITE" in actions
        assert "VERDICT_APPROVE" in actions
        assert verify_chain(session).valid is True


def test_the_chain_stays_valid_across_several_cycles() -> None:
    """Phases commit separately, so the chain has to survive interleaving."""
    for _ in range(3):
        run(FakeLLM(decision_json()))

    with session_scope() as session:
        result = verify_chain(session)
        assert result.valid is True
        # Three cycles over an identical snapshot: the snapshot is recorded
        # once, the candidates and decisions every time.
        assert result.records_checked >= 7


def test_the_audit_record_never_leaks_a_signature() -> None:
    run(FakeLLM(write_json(candidate_id())))

    with session_scope() as session:
        records = session.execute(select(AuditLog)).scalars().all()
        for row in records:
            assert "signature" not in str(row.after_json or {})
        verdict = session.execute(select(KernelDecision)).scalar_one()
        assert verdict.signature  # stored, but never in the audit payload


# ---------------------------------------------------------------------------
# Execution writes a policy, a reserve, and an order
# ---------------------------------------------------------------------------


def executing_cycle(broker: FakeBroker):  # type: ignore[no-untyped-def]
    engine = ExecutionEngine(
        broker,
        secret=SECRET,
        sleep=lambda _s: None,
        rate_limiter=TokenBucket(sleep=lambda _s: None),
    )
    return run(FakeLLM(write_json(candidate_id())), dry_run=False, execution=engine)


def test_a_filled_order_writes_a_policy_a_reserve_and_an_order() -> None:
    report = executing_cycle(FakeBroker(states=[OrderState.FILLED]))
    assert report.execution_status == str(ExecutionStatus.FILLED)

    with session_scope() as session:
        policy = session.execute(select(Policy)).scalar_one()
        reserve = session.execute(select(Reserve)).scalar_one()
        order = session.execute(select(Order)).scalar_one()

        assert policy.status == "OPEN"
        assert policy.policy_number.startswith("UW-")
        assert reserve.status == "HELD"
        # SK-002: reserved at exactly max loss.
        assert reserve.amount == policy.max_loss
        # NFR-008: the order is traceable to the verdict that authorised it.
        assert order.kernel_decision_id
        assert order.policy_id == policy.id


def test_the_reserve_invariant_holds_after_a_cycle() -> None:
    """DB-INV-1 the moment a policy exists, not only at reconciliation."""
    executing_cycle(FakeBroker(states=[OrderState.FILLED]))

    with session_scope() as session:
        invariant = check_reserve_invariant(session)
        assert invariant.holds, invariant.detail
        assert invariant.held_reserves > Decimal("0")


def test_a_rejected_order_writes_no_policy() -> None:
    """A position the broker never took must not appear on the book."""
    executing_cycle(FakeBroker(states=[OrderState.REJECTED]))

    assert count(Policy) == 0
    assert count(Reserve) == 0

    with session_scope() as session:
        event = session.execute(select(RiskEvent)).scalar_one()
        assert event.event_type == "ORDER_NOT_FILLED"


def test_a_partial_fill_is_flagged_leg_risk_and_raised_as_critical() -> None:
    """F-13: one leg on and one leg off is uncovered short exposure."""
    executing_cycle(
        FakeBroker(states=[OrderState.CANCELED], filled_qty=Decimal("1"), ordered_qty=Decimal("2"))
    )

    with session_scope() as session:
        policy = session.execute(select(Policy)).scalar_one()
        assert policy.status == "LEG_RISK"

        event = session.execute(
            select(RiskEvent).where(RiskEvent.event_type == "LEG_RISK")
        ).scalar_one()
        assert event.severity == "CRITICAL"


def test_policy_numbers_are_sequential_and_human_readable() -> None:
    with session_scope() as session:
        first = next_policy_number(session)
        assert first == f"UW-{datetime.now(UTC).year}-0001"


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_reconciliation_records_an_equity_point_and_verifies_the_invariant() -> None:
    report = run_reconcile_cycle(FakeBroker(), equity=Decimal("100000"))

    assert report.status is CycleStatus.SUCCESS
    assert report.invariant_holds is True

    with session_scope() as session:
        point = session.execute(select(PnlRecord)).scalar_one()
        assert point.equity == Decimal("100000")
        assert verify_chain(session).valid is True


def test_an_orphan_position_is_critical_and_forces_manage_only() -> None:
    """F-19: the broker holds something no policy explains."""
    broker = FakeBroker(positions=[BrokerPosition(symbol="SPY260918P00550000", qty=Decimal("-2"))])
    report = run_reconcile_cycle(broker, equity=Decimal("100000"))

    assert report.orphans == ("SPY260918P00550000",)
    assert report.forced_manage_only is True
    assert report.healthy is False

    with session_scope() as session:
        event = session.execute(
            select(RiskEvent).where(RiskEvent.event_type == "ORPHAN_POSITION")
        ).scalar_one()
        assert event.severity == "CRITICAL"


def test_a_broken_invariant_forces_manage_only() -> None:
    """F-25: the book's own accounting disagrees with itself."""
    with session_scope() as session:
        session.add(
            Policy(
                policy_number="UW-2026-9999",
                correlation_id="c",
                underlying="SPY",
                structure="PUT_CREDIT_SPREAD",
                status="OPEN",
                max_loss=Decimal("500.00"),
                capital_reserve=Decimal("500.00"),
            )
        )  # no reserve row: under-held

    report = run_reconcile_cycle(FakeBroker(), equity=Decimal("100000"))

    assert report.invariant_holds is False
    assert report.forced_manage_only is True
    assert "under-held" in report.invariant_detail


def test_reconciliation_snapshots_what_the_broker_holds() -> None:
    broker = FakeBroker(positions=[BrokerPosition(symbol="SPY_X", qty=Decimal("-1"))])
    run_reconcile_cycle(broker, equity=Decimal("100000"))

    with session_scope() as session:
        snapshot = session.execute(select(PositionSnapshot)).scalar_one()
        assert snapshot.symbol == "SPY_X"
        assert snapshot.matched_policy_id is None  # orphan


def test_a_broker_that_cannot_be_read_is_an_error_not_a_crash() -> None:
    class Unreachable(FakeBroker):
        def list_positions(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("positions endpoint down")

    report = run_reconcile_cycle(Unreachable())
    assert report.status is CycleStatus.ERROR
    assert "positions endpoint down" in report.detail


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


def test_settling_a_policy_releases_its_reserve() -> None:
    """A settled policy with a HELD reserve breaks DB-INV-1 on the next pass."""
    executing_cycle(FakeBroker(states=[OrderState.FILLED]))

    with session_scope() as session:
        policy = session.execute(select(Policy)).scalar_one()
        CycleRecorder(session, "cyc_settle").settle(
            policy.id,
            closing_debit=Decimal("0.25"),
            realized=Decimal("50.00"),
            reason="PROFIT_TARGET",
        )

    with session_scope() as session:
        policy = session.execute(select(Policy)).scalar_one()
        reserve = session.execute(select(Reserve)).scalar_one()

        assert policy.status == "SETTLED"
        assert policy.realized_pnl == Decimal("50.00")
        assert policy.outcome_win == 1
        assert reserve.status == "RELEASED"
        assert check_reserve_invariant(session).holds is True


def test_settling_a_policy_that_does_not_exist_is_a_no_op() -> None:
    with session_scope() as session:
        CycleRecorder(session, "c").settle(
            "missing", closing_debit=Decimal("0"), realized=Decimal("0"), reason="MANUAL"
        )


def test_the_dashboard_reads_what_the_cycle_wrote() -> None:
    """End to end: a cycle runs, and the overview stops being empty."""
    from underwriter.controllers import dashboard_controller

    assert dashboard_controller.overview()["empty"] is True

    executing_cycle(FakeBroker(states=[OrderState.FILLED]))
    overview = dashboard_controller.overview()

    assert overview["empty"] is False
    assert overview["book"]["open_policies"] == 1
    assert Decimal(overview["capital"]["reserved"]) > Decimal("0")
    assert PORTFOLIO.nav > Decimal("0")
    assert kernel.DEFAULT_LIMITS.max_open_policies == 8


# ---------------------------------------------------------------------------
# API-033 — the determinism proof
# ---------------------------------------------------------------------------


def test_a_recorded_decision_replays_with_an_empty_diff() -> None:
    """NFR-007 and AC-08, end to end over stored inputs.

    Only possible because of choices made much earlier: the Actuary takes its
    clock from the snapshot, every value is Decimal, and the snapshot hashes
    stably. Any one of those missing and this reports drift on every call.
    """
    from underwriter.cycle.replay import replay_decision

    run(FakeLLM(write_json(candidate_id())))

    with session_scope() as session:
        verdict = session.execute(select(KernelDecision)).scalar_one()
        result = replay_decision(session, verdict.id)

    assert result.deterministic is True, result.detail
    assert result.diff == ()
    assert result.replayed_hash == result.snapshot_hash


def test_replay_detects_a_tampered_snapshot() -> None:
    """Editing the stored chain must not replay clean."""
    from underwriter.cycle.replay import replay_decision

    run(FakeLLM(write_json(candidate_id())))

    with session_scope() as session:
        row = session.execute(select(MarketSnapshotRow)).scalar_one()
        payload = dict(row.chain_json)
        quotes = [dict(q) for q in payload["quotes"]]
        quotes[0]["bid"] = "9.99"  # a plausible-looking edit
        payload["quotes"] = quotes
        row.chain_json = payload

    with session_scope() as session:
        verdict = session.execute(select(KernelDecision)).scalar_one()
        result = replay_decision(session, verdict.id)

    assert result.deterministic is False
    assert "no longer hashes" in result.detail


def test_replay_refuses_a_decision_it_cannot_reconstruct() -> None:
    from underwriter.cycle.replay import ReplayUnavailableError, replay_decision

    with session_scope() as session, pytest.raises(ReplayUnavailableError, match="no kernel"):
        replay_decision(session, "does-not-exist")


def test_a_replay_mismatch_raises_a_critical_risk_event() -> None:
    """Determinism breaking is a reason to stop trading, not a log line."""
    from underwriter.controllers import underwriting_controller

    run(FakeLLM(write_json(candidate_id())))

    with session_scope() as session:
        row = session.execute(select(MarketSnapshotRow)).scalar_one()
        payload = dict(row.chain_json)
        quotes = [dict(q) for q in payload["quotes"]]
        quotes[0]["ask"] = "8.88"
        payload["quotes"] = quotes
        row.chain_json = payload
        verdict_id = session.execute(select(KernelDecision)).scalar_one().id

    body = underwriting_controller.replay(verdict_id)
    assert body["deterministic"] is False

    with session_scope() as session:
        event = session.execute(
            select(RiskEvent).where(RiskEvent.event_type == "REPLAY_MISMATCH")
        ).scalar_one()
        assert event.severity == "CRITICAL"

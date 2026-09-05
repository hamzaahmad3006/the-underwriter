"""Claims Desk and cycle orchestration — FR-100 … FR-107, §12, §15.4.

§15.4's precedence is the thing under test. Precedence only matters when
several reasons are true at once, so most of these set up exactly that and
assert which one wins.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import SECRET, make_context, make_proposal
from tests.test_agent import FakeLLM, decision_json, write_json
from tests.test_data_layer import FakeSource, raw
from underwriter.agent.underwriter import AIUnderwriter
from underwriter.claims.desk import (
    ClaimsPolicy,
    ExitReason,
    ManagedPosition,
    evaluate,
    loss_ratio,
    realized_pnl,
)
from underwriter.cycle.manage import run_management_cycle
from underwriter.cycle.scheduler import CycleScheduler, JobResult
from underwriter.cycle.underwrite import CycleStatus, run_underwriting_cycle
from underwriter.data.snapshot import SnapshotConfig
from underwriter.db import create_all, reset_engine

TODAY = date(2026, 9, 1)
FAR = date(2026, 9, 18)  # 17 DTE
NEAR = date(2026, 9, 3)  # 2 DTE — inside the force-flat floor
SOLO = SnapshotConfig(universe=("SPY",))


def position(
    *,
    expiry: date = FAR,
    opening_credit: str = "0.50",
    cost_to_close: str | None = "0.40",
    underlying_price: str | None = "570",
    short_strike: str = "550",
    contracts: int = 2,
) -> ManagedPosition:
    return ManagedPosition(
        policy_id="pol_1",
        policy_number="UW-2026-0001",
        underlying="SPY",
        contracts=contracts,
        opening_credit=Decimal(opening_credit),
        max_loss=Decimal("150.00"),
        short_strike=Decimal(short_strike),
        long_strike=Decimal("548"),
        expiry=expiry,
        cost_to_close=None if cost_to_close is None else Decimal(cost_to_close),
        underlying_price=None if underlying_price is None else Decimal(underlying_price),
    )


# ---------------------------------------------------------------------------
# §15.4 precedence
# ---------------------------------------------------------------------------


def test_103_force_flat_closes_unconditionally_at_the_dte_floor() -> None:
    """FR-103. No P&L check, no price needed, no exceptions."""
    verdict = evaluate(position(expiry=NEAR), TODAY)

    assert verdict.should_close is True
    assert verdict.reason is ExitReason.FORCE_FLAT
    assert "cannot measure" in verdict.detail


def test_103_force_flat_beats_a_position_sitting_in_profit() -> None:
    """A profitable unmeasurable position is still unmeasurable."""
    winning = position(expiry=NEAR, cost_to_close="0.05")
    assert evaluate(winning, TODAY).reason is ExitReason.FORCE_FLAT


def test_103_force_flat_works_when_the_spread_cannot_be_priced() -> None:
    """The case it exists for: Greeks and quotes vanish near expiry."""
    verdict = evaluate(position(expiry=NEAR, cost_to_close=None), TODAY)

    assert verdict.should_close is True
    assert verdict.reason is ExitReason.FORCE_FLAT
    # Falls back to the width — the most the close could possibly cost.
    assert verdict.target_debit == Decimal("2")


def test_102_stop_loss_beats_a_breach() -> None:
    """Both are true; the stop wins because it is further down the list."""
    stopped = position(cost_to_close="1.00", underlying_price="545")  # 2x credit
    verdict = evaluate(stopped, TODAY)

    assert verdict.reason is ExitReason.STOP_LOSS
    assert "2.0x" in verdict.detail or "2.0" in verdict.detail


def test_104_a_breach_closes_and_escalates() -> None:
    """SK-013: no new policies on that underlying either."""
    breached = position(cost_to_close="0.60", underlying_price="549")
    verdict = evaluate(breached, TODAY)

    assert verdict.reason is ExitReason.BREACH
    assert verdict.escalate is True


def test_104_a_breach_already_at_the_profit_target_closes_for_profit() -> None:
    """Rule 4 closes it anyway, and for a better reason."""
    breached_but_winning = position(cost_to_close="0.20", underlying_price="549")
    assert evaluate(breached_but_winning, TODAY).reason is ExitReason.PROFIT_TARGET


def test_101_the_profit_target_closes_at_half_the_credit() -> None:
    verdict = evaluate(position(cost_to_close="0.25"), TODAY)
    assert verdict.reason is ExitReason.PROFIT_TARGET

    # Exactly at the boundary still closes.
    assert evaluate(position(cost_to_close="0.25"), TODAY).should_close is True
    assert evaluate(position(cost_to_close="0.26"), TODAY).should_close is False


def test_a_healthy_position_is_held() -> None:
    verdict = evaluate(position(cost_to_close="0.40"), TODAY)
    assert verdict.should_close is False
    assert verdict.reason is None
    assert "holding" in verdict.detail


def test_an_unpriceable_position_is_escalated_not_silently_held() -> None:
    """A position we cannot judge is not the same as one we judged as fine."""
    verdict = evaluate(position(cost_to_close=None), TODAY)

    assert verdict.should_close is False
    assert verdict.escalate is True
    assert "unavailable" in verdict.detail


def test_a_missing_underlying_price_is_not_read_as_a_breach() -> None:
    assert position(underlying_price=None).is_breached is False


@pytest.mark.parametrize(
    ("profit_target", "cost", "closes"),
    [(Decimal("0.50"), "0.25", True), (Decimal("0.75"), "0.25", False)],
)
def test_the_profit_target_is_configurable(profit_target: Decimal, cost: str, closes: bool) -> None:
    policy = ClaimsPolicy(profit_target_pct=profit_target)
    assert evaluate(position(cost_to_close=cost), TODAY, policy).should_close is closes


# ---------------------------------------------------------------------------
# FR-107 — settlement arithmetic
# ---------------------------------------------------------------------------


def test_107_realized_pnl_is_credit_minus_debit_times_the_multiplier() -> None:
    assert realized_pnl(Decimal("0.50"), Decimal("0.25"), 2) == Decimal("50.00")
    assert realized_pnl(Decimal("0.50"), Decimal("1.00"), 2) == Decimal("-100.00")


def test_107_unrealized_pnl_tracks_the_open_position() -> None:
    assert position(cost_to_close="0.30").unrealized_pnl() == Decimal("40.00")
    assert position(cost_to_close=None).unrealized_pnl() is None


def test_the_loss_ratio_is_the_underwriting_measure() -> None:
    assert loss_ratio(Decimal("300"), Decimal("1000")) == Decimal("0.3")
    assert loss_ratio(Decimal("300"), Decimal("0")) is None


# ---------------------------------------------------------------------------
# The management cycle — FR-106
# ---------------------------------------------------------------------------


def test_106_an_exit_is_routed_through_the_kernel() -> None:
    proposal = make_proposal()
    report = run_management_cycle(
        (position(expiry=NEAR),),
        proposals_by_policy={"pol_1": proposal},
        context=make_context(),
        secret=SECRET,
        as_of=TODAY,
        dry_run=True,
    )

    assert report.evaluated == 1
    assert report.attempts[0].verdict == "APPROVE"
    assert "nothing transmitted" in report.attempts[0].detail


def test_106_a_close_is_approved_even_when_capital_is_exhausted() -> None:
    """SK-000 through the whole management path, not just the rule."""
    from tests.conftest import make_policy

    exhausted = make_context(
        open_policies=tuple(make_policy(policy_id=f"p{i}", max_loss="12000.00") for i in range(5))
    )
    report = run_management_cycle(
        (position(expiry=NEAR),),
        proposals_by_policy={"pol_1": make_proposal()},
        context=exhausted,
        secret=SECRET,
        as_of=TODAY,
        dry_run=True,
    )
    assert report.attempts[0].verdict == "APPROVE"


def test_106_a_refused_close_is_surfaced_loudly() -> None:
    """The position stays on the book, so the reason has to be visible."""
    report = run_management_cycle(
        (position(expiry=NEAR),),
        proposals_by_policy={"pol_1": make_proposal()},
        context=make_context(market_open=False),
        secret=SECRET,
        as_of=TODAY,
        dry_run=True,
    )

    assert report.attempts[0].verdict == "REJECT"
    assert "MARKET_CLOSED" in report.attempts[0].reject_reasons


def test_a_held_position_never_reaches_the_kernel() -> None:
    report = run_management_cycle(
        (position(cost_to_close="0.40"),),
        proposals_by_policy={"pol_1": make_proposal()},
        context=make_context(),
        secret=SECRET,
        as_of=TODAY,
        dry_run=True,
    )

    assert report.held == 1
    assert report.attempts[0].verdict is None


def test_a_policy_with_no_stored_proposal_cannot_be_closed() -> None:
    report = run_management_cycle(
        (position(expiry=NEAR),),
        proposals_by_policy={},
        context=make_context(),
        secret=SECRET,
        as_of=TODAY,
        dry_run=True,
    )
    assert "no stored proposal" in report.attempts[0].detail


def test_escalations_are_collected_across_the_whole_cycle() -> None:
    breached = ManagedPosition(
        policy_id="pol_2",
        policy_number="UW-2026-0002",
        underlying="QQQ",
        contracts=1,
        opening_credit=Decimal("0.50"),
        max_loss=Decimal("150"),
        short_strike=Decimal("470"),
        long_strike=Decimal("465"),
        expiry=FAR,
        cost_to_close=Decimal("0.60"),
        underlying_price=Decimal("469"),
    )
    report = run_management_cycle(
        (breached,),
        proposals_by_policy={},
        context=make_context(),
        secret=SECRET,
        as_of=TODAY,
        dry_run=True,
    )
    assert report.escalations
    assert "UW-2026-0002" in report.escalations[0]


def test_an_empty_book_is_a_no_action_cycle() -> None:
    report = run_management_cycle(
        (), proposals_by_policy={}, context=make_context(), secret=SECRET, as_of=TODAY
    )
    assert report.status is CycleStatus.NO_ACTION


# ---------------------------------------------------------------------------
# The underwriting cycle — §12.1
# ---------------------------------------------------------------------------


def chain() -> FakeSource:
    return FakeSource(
        contracts=[
            raw(symbol="SPY_550", strike="550", bid="2.00", ask="2.10", delta="-0.20"),
            raw(symbol="SPY_548", strike="548", bid="1.40", ask="1.50", delta="-0.15"),
        ]
    )


def run(llm: FakeLLM, **kwargs: object):  # type: ignore[no-untyped-def]
    return run_underwriting_cycle(
        source=kwargs.pop("source", None) or chain(),  # type: ignore[arg-type]
        agent=AIUnderwriter(llm),
        context=kwargs.pop("context", None) or make_context(),  # type: ignore[arg-type]
        secret=SECRET,
        snapshot_config=SOLO,
        dry_run=True,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_full_pipeline_reaches_an_approved_verdict() -> None:
    """Data, Actuary, model, Kernel — in one cycle, with nothing transmitted."""
    priced = chain()
    from underwriter.actuary.engine import price_put_credit_spreads
    from underwriter.data.snapshot import build_snapshot

    snapshot = build_snapshot(priced, config=SOLO).snapshot
    assert snapshot is not None
    candidate_id = price_put_credit_spreads(snapshot).proposals[0].candidate_id

    report = run(FakeLLM(write_json(candidate_id)))

    assert report.status is CycleStatus.SUCCESS
    assert report.outcome == "DRY_RUN_APPROVED"
    assert report.verdict == "APPROVE"
    assert report.candidates_priced == 1
    assert report.steps == (
        "fetch_snapshot",
        "price_candidates",
        "underwrite",
        "adjudicate",
        "dry_run",
    )


def test_a_declined_cycle_is_a_success_not_a_failure() -> None:
    """FR-026, as the status field."""
    report = run(FakeLLM(decision_json()))

    assert report.status is CycleStatus.NO_ACTION
    assert report.outcome == "DECLINED"
    assert report.traded is False


def test_a_closed_market_aborts_before_any_llm_call() -> None:
    llm = FakeLLM()
    report = run(llm, source=FakeSource(is_open=False))

    assert report.status is CycleStatus.ABORTED
    assert report.outcome == "MARKET_CLOSED"
    assert llm.calls == [], "the model was asked about a closed market"


def test_no_qualifying_candidates_ends_the_cycle_before_the_model() -> None:
    llm = FakeLLM()
    report = run(llm, source=FakeSource(contracts=[raw(symbol="A", delta=None)]))

    assert report.status is CycleStatus.NO_ACTION
    assert report.outcome == "NO_QUALIFYING_CANDIDATES"
    assert llm.calls == []


def test_a_hallucinated_candidate_aborts_the_cycle() -> None:
    report = run(FakeLLM(write_json("cand_not_real"), write_json("cand_still_not_real")))

    assert report.status is CycleStatus.ABORTED
    assert report.outcome == "LLM_SCHEMA_VIOLATION"
    assert report.verdict is None, "an aborted decision never reached the Kernel"


def test_every_cycle_carries_one_correlation_id() -> None:
    report = run(FakeLLM(decision_json()), correlation_id="cyc_fixed")
    assert report.correlation_id == "cyc_fixed"
    assert report.duration_ms is not None


def test_an_unexpected_fault_is_recorded_rather_than_raised() -> None:
    """The scheduler must survive any cycle."""

    class Exploding:
        def get_session(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("clock service on fire")

        def get_option_chain(self, *a, **k):  # type: ignore[no-untyped-def]
            return []

        def get_daily_closes(self, *a, **k):  # type: ignore[no-untyped-def]
            return []

    report = run(FakeLLM(), source=Exploding())
    assert report.status is CycleStatus.ERROR
    assert report.outcome == "RuntimeError"


# ---------------------------------------------------------------------------
# The scheduler
# ---------------------------------------------------------------------------


@pytest.fixture
def scheduler_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    create_all()
    yield
    reset_engine()


def test_a_job_that_raises_never_escapes_the_scheduler(scheduler_db: None) -> None:
    """A throwing job would kill its own trigger and silently stop the desk."""

    def explode(correlation_id: str) -> JobResult:
        raise RuntimeError("cycle blew up")

    scheduler = CycleScheduler(underwrite=explode)
    result = scheduler.run_job("underwrite")

    assert result.status is CycleStatus.ERROR
    assert result.outcome == "RuntimeError"


def test_every_run_is_recorded_including_the_quiet_ones(scheduler_db: None) -> None:
    from underwriter.db import session_scope
    from underwriter.db.models import SchedulerRun

    scheduler = CycleScheduler(
        underwrite=lambda _cid: JobResult(CycleStatus.NO_ACTION, "NO_QUALIFYING_CANDIDATES")
    )
    scheduler.run_job("underwrite")

    with session_scope() as session:
        run_row = session.query(SchedulerRun).one()
        assert run_row.status == "NO_ACTION"
        assert run_row.outcome == "NO_QUALIFYING_CANDIDATES"
        assert run_row.duration_ms is not None


def test_consecutive_failures_are_counted_and_reset(scheduler_db: None) -> None:
    """ERR-006 alerts on the third, not the first."""
    outcomes = [
        JobResult(CycleStatus.ERROR, "boom"),
        JobResult(CycleStatus.ERROR, "boom"),
        JobResult(CycleStatus.SUCCESS, "fine"),
    ]
    scheduler = CycleScheduler(manage=lambda _cid: outcomes.pop(0))

    scheduler.run_job("manage")
    scheduler.run_job("manage")
    assert scheduler.status()["jobs"][0]["consecutive_failures"] == 2

    scheduler.run_job("manage")
    assert scheduler.status()["jobs"][0]["consecutive_failures"] == 0


def test_an_unknown_job_is_an_error_not_a_crash(scheduler_db: None) -> None:
    scheduler = CycleScheduler()
    assert scheduler.run_job("nonexistent").outcome == "UNKNOWN_JOB"


def test_the_scheduler_reports_its_own_state(scheduler_db: None) -> None:
    scheduler = CycleScheduler(underwrite=lambda _cid: JobResult(CycleStatus.SUCCESS, "ok"))
    status = scheduler.status()

    assert status["running"] is False
    assert status["jobs"][0]["name"] == "underwrite"
    assert status["jobs"][0]["interval_min"] == 30


def test_a_recording_failure_does_not_take_the_cycle_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the log is bad. Losing the scheduler is worse."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()  # no create_all: the tables do not exist

    scheduler = CycleScheduler(underwrite=lambda _cid: JobResult(CycleStatus.SUCCESS, "ok"))
    assert scheduler.run_job("underwrite").status is CycleStatus.SUCCESS

    reset_engine()


def test_the_cadences_match_the_srs() -> None:
    """§13.2: underwrite 30, manage 15, reconcile 5."""
    from underwriter.cycle.scheduler import (
        MANAGE_INTERVAL_MIN,
        RECONCILE_INTERVAL_MIN,
        UNDERWRITE_INTERVAL_MIN,
    )

    assert (UNDERWRITE_INTERVAL_MIN, MANAGE_INTERVAL_MIN, RECONCILE_INTERVAL_MIN) == (30, 15, 5)


def test_force_flat_leaves_no_room_to_reach_zero_dte() -> None:
    """FR-103 and SK-011 together: entry at 7 DTE, flat at 2, never 0."""
    from underwriter.claims.desk import FORCE_FLAT_DTE
    from underwriter.kernel.limits import DEFAULT_LIMITS

    assert DEFAULT_LIMITS.min_dte_at_entry > FORCE_FLAT_DTE
    assert FORCE_FLAT_DTE > 0
    assert (TODAY + timedelta(days=FORCE_FLAT_DTE)) > TODAY
    assert datetime.now(UTC).tzinfo is UTC


# ---------------------------------------------------------------------------
# MCP-001 — the flag has to be switched on by something
# ---------------------------------------------------------------------------


def test_the_cycle_asks_mcp_for_context_when_told_to() -> None:
    """MCP-001. Off by default so tests and dry runs spawn no subprocess, which
    means something has to turn it on — and for a while nothing did."""
    calls: list[str] = []

    def fake_context():  # type: ignore[no-untyped-def]
        from underwriter.mcp.context import MarketContext

        calls.append("fetched")
        return MarketContext(account={"equity": "100000.00"}, tools_used=("get_account_info",))

    import underwriter.mcp as mcp_package

    original = mcp_package.fetch_context
    mcp_package.fetch_context = fake_context  # type: ignore[assignment]
    try:
        run_underwriting_cycle(
            source=chain(),
            agent=AIUnderwriter(FakeLLM(decision_json())),
            context=make_context(),
            secret=SECRET,
            snapshot_config=SOLO,
            dry_run=True,
            use_mcp=True,
        )
    finally:
        mcp_package.fetch_context = original  # type: ignore[assignment]

    assert calls == ["fetched"], "the cycle did not consult MCP for context"


def test_the_cycle_spawns_no_subprocess_when_mcp_is_off() -> None:
    """The default path must stay free of a subprocess spawn."""
    calls: list[str] = []

    import underwriter.mcp as mcp_package

    original = mcp_package.fetch_context
    mcp_package.fetch_context = lambda: calls.append("fetched")  # type: ignore[assignment,return-value]
    try:
        run_underwriting_cycle(
            source=chain(),
            agent=AIUnderwriter(FakeLLM(decision_json())),
            context=make_context(),
            secret=SECRET,
            snapshot_config=SOLO,
            dry_run=True,
        )
    finally:
        mcp_package.fetch_context = original  # type: ignore[assignment]

    assert calls == []


def test_the_bootstrap_turns_mcp_on_when_the_server_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring that was missing: availability decides, not a default."""
    from underwriter.cycle import bootstrap

    monkeypatch.setenv("KERNEL_SIGNING_SECRET", SECRET)
    monkeypatch.setattr("underwriter.mcp.is_available", lambda: True)
    assert bootstrap.build().uses_mcp is True

    monkeypatch.setattr("underwriter.mcp.is_available", lambda: False)
    wiring = bootstrap.build()
    assert wiring.uses_mcp is False
    assert any("MCP server is not installed" in note for note in wiring.notes)

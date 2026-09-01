"""Persistence — §18 schema, the audit hash chain, and DB-INV-1.

Three of these tests are about constraints rather than code. `accounts.is_paper`,
`orders.kernel_decision_id` and `kernel_decisions.nonce` each turn a rule the
SRS states in prose into something the database refuses, and a constraint
nobody tested is a constraint nobody knows is armed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError, StatementError

from underwriter.audit.ledger import (
    GENESIS_HASH,
    Actor,
    append,
    compute_hash,
    latest_hash,
    verify_chain,
)
from underwriter.db import create_all, reset_engine, session_scope
from underwriter.db.base import Money, UtcTimestamp
from underwriter.db.invariants import check_reserve_invariant
from underwriter.db.models import (
    Account,
    AuditLog,
    KernelDecision,
    Order,
    Policy,
    Reserve,
    SchedulerRun,
    SystemConfig,
)

NOW = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A fresh database per test. Never touches the real volume."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    create_all()
    yield
    reset_engine()


def make_verdict(session, *, nonce: str = "nonce-1", verdict: str = "APPROVE") -> KernelDecision:
    row = KernelDecision(
        correlation_id="corr-1",
        proposal_hash="a" * 64,
        verdict=verdict,
        approved_contracts=2,
        nonce=nonce,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=45),
        signature="sig" if verdict == "APPROVE" else None,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# The constraints that carry a requirement
# ---------------------------------------------------------------------------


def test_a_live_account_cannot_be_recorded_at_all() -> None:
    """DB-002's CHECK(is_paper = 1) — the paper guarantee survives bad config."""
    with pytest.raises(IntegrityError), session_scope() as session:
        session.add(Account(alpaca_account_id="LIVE123", is_paper=0))


def test_a_paper_account_records_its_baseline() -> None:
    """ALP-002: the immutable baseline every drawdown figure is measured from."""
    with session_scope() as session:
        session.add(
            Account(
                alpaca_account_id="PA3VUA0IEINK",
                is_paper=1,
                baseline_equity=Decimal("100000.00"),
                baseline_at=NOW,
            )
        )

    with session_scope() as session:
        account = session.query(Account).one()
        assert account.baseline_equity == Decimal("100000.00")
        assert account.baseline_at == NOW


def test_an_order_cannot_exist_without_the_verdict_that_authorised_it() -> None:
    """NFR-008 as a schema property, not a promise.

    This is the persistence half of the Kernel's central claim: even if every
    layer above were bypassed, there is no row shape that records an order
    with no verdict behind it.
    """
    with pytest.raises((IntegrityError, StatementError)), session_scope() as session:
        session.add(
            Order(
                client_order_id="uw_orphan",
                kernel_decision_id=None,
                intent="ENTRY",
            )
        )


def test_a_nonce_cannot_be_used_twice() -> None:
    """§14.4 mechanism 5, enforced by the database itself (TEST-034)."""
    with session_scope() as session:
        make_verdict(session, nonce="nonce-single-use")

    with pytest.raises(IntegrityError), session_scope() as session:
        make_verdict(session, nonce="nonce-single-use")


def test_a_client_order_id_cannot_be_reused() -> None:
    """FR-083: a retry after an ambiguous failure collides instead of doubling."""
    with session_scope() as session:
        verdict = make_verdict(session, nonce="n-coid")
        session.add(Order(client_order_id="uw_dup", kernel_decision_id=verdict.id, intent="ENTRY"))

    with pytest.raises(IntegrityError), session_scope() as session:
        verdict = make_verdict(session, nonce="n-coid-2")
        session.add(Order(client_order_id="uw_dup", kernel_decision_id=verdict.id, intent="ENTRY"))


def test_system_config_is_genuinely_single_row() -> None:
    with pytest.raises(IntegrityError), session_scope() as session:
        session.add(SystemConfig(id=2, mode="ACTIVE"))


def test_an_invalid_mode_is_refused() -> None:
    with pytest.raises(IntegrityError), session_scope() as session:
        session.add(SystemConfig(id=1, mode="YOLO"))


def test_no_action_is_a_distinct_scheduler_outcome_from_error() -> None:
    """FR-026 as data: a cycle that declined to trade succeeded."""
    with session_scope() as session:
        session.add(
            SchedulerRun(
                job_name="underwrite",
                status="NO_ACTION",
                outcome="NO_QUALIFYING_CANDIDATES",
            )
        )

    with session_scope() as session:
        assert session.query(SchedulerRun).one().status == "NO_ACTION"


# ---------------------------------------------------------------------------
# Column types — NFR-012, NFR-013
# ---------------------------------------------------------------------------


def test_money_round_trips_as_an_exact_decimal() -> None:
    with session_scope() as session:
        session.add(
            Policy(
                policy_number="UW-2026-0001",
                correlation_id="c",
                underlying="SPY",
                structure="PUT_CREDIT_SPREAD",
                status="OPEN",
                max_loss=Decimal("150.01"),
                capital_reserve=Decimal("150.01"),
            )
        )

    with session_scope() as session:
        policy = session.query(Policy).one()
        assert policy.max_loss == Decimal("150.01")
        assert isinstance(policy.max_loss, Decimal)


def test_a_float_is_refused_as_money() -> None:
    """The whole reason Money is a type and not a convention."""
    with pytest.raises(TypeError, match="NFR-013"):
        Money().process_bind_param(0.1 + 0.2, None)  # type: ignore[arg-type]


def test_a_non_finite_money_value_is_refused() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        Money().process_bind_param(Decimal("NaN"), None)  # type: ignore[arg-type]


def test_a_naive_timestamp_is_refused() -> None:
    """NFR-012: a naive datetime has already lost what UTC storage needs."""
    with pytest.raises(ValueError, match="NFR-012"):
        UtcTimestamp().process_bind_param(datetime(2026, 9, 1, 15, 30), None)  # type: ignore[arg-type]


def test_timestamps_are_stored_as_iso_utc_with_z() -> None:
    stored = UtcTimestamp().process_bind_param(NOW, None)  # type: ignore[arg-type]
    assert stored == "2026-09-01T15:30:00Z"
    assert UtcTimestamp().process_result_value(stored, None) == NOW  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The audit hash chain — DB-017, API-061
# ---------------------------------------------------------------------------


def test_an_empty_ledger_verifies() -> None:
    with session_scope() as session:
        result = verify_chain(session)
        assert result.valid is True
        assert result.records_checked == 0


def test_the_first_record_links_to_genesis() -> None:
    with session_scope() as session:
        assert latest_hash(session) == GENESIS_HASH
        record = append(session, actor=Actor.KERNEL, action="VERDICT_MINTED")
        assert record.prev_hash == GENESIS_HASH


def test_each_record_links_to_the_one_before_it() -> None:
    with session_scope() as session:
        first = append(session, actor=Actor.ACTUARY, action="CANDIDATES_PRICED")
        second = append(session, actor=Actor.KERNEL, action="VERDICT_MINTED")
        third = append(session, actor=Actor.EXECUTION, action="ORDER_SUBMITTED")

        assert second.prev_hash == first.record_hash
        assert third.prev_hash == second.record_hash


def test_a_populated_chain_verifies_end_to_end() -> None:
    with session_scope() as session:
        for index in range(20):
            append(
                session,
                actor=Actor.SCHEDULER,
                action="CYCLE_COMPLETE",
                entity_id=f"run-{index}",
                after={"index": index},
                correlation_id=f"corr-{index}",
            )

    with session_scope() as session:
        result = verify_chain(session)
        assert result.valid is True
        assert result.records_checked == 20
        assert result.first_break_seq is None


def test_editing_a_record_breaks_the_chain_at_that_record() -> None:
    """Tampering is detectable, which is the entire point of the chain."""
    with session_scope() as session:
        for index in range(5):
            append(session, actor=Actor.OPERATOR, action="MODE_CHANGE", after={"i": index})

    with session_scope() as session:
        row = session.query(AuditLog).filter(AuditLog.seq == 3).one()
        row.after_json = {"i": 999}  # a plausible-looking edit

    with session_scope() as session:
        result = verify_chain(session)
        assert result.valid is False
        assert result.first_break_seq == 3
        assert "was edited" in result.detail


def test_deleting_a_record_breaks_the_chain_after_the_gap() -> None:
    with session_scope() as session:
        for index in range(5):
            append(session, actor=Actor.CLAIMS, action="POLICY_SETTLED", after={"i": index})

    with session_scope() as session:
        session.query(AuditLog).filter(AuditLog.seq == 2).delete()

    with session_scope() as session:
        result = verify_chain(session)
        assert result.valid is False
        assert result.first_break_seq == 3
        assert "inserted, removed or reordered" in result.detail


def test_the_record_hash_covers_the_content_and_the_previous_hash() -> None:
    """Same content under a different predecessor must hash differently."""
    from underwriter.audit.ledger import record_payload

    common = {
        "occurred_at": NOW,
        "correlation_id": "c",
        "actor": "KERNEL",
        "action": "VERDICT_MINTED",
        "entity_type": "kernel_decision",
        "entity_id": "k1",
        "before": None,
        "after": {"verdict": "APPROVE"},
    }
    a = compute_hash(record_payload(**common, prev_hash="a" * 64))
    b = compute_hash(record_payload(**common, prev_hash="b" * 64))
    assert a != b


def test_the_operator_is_recorded_like_any_other_actor() -> None:
    """SEC-012: an operator action enters the same ledger as the scheduler's."""
    with session_scope() as session:
        append(
            session,
            actor=Actor.OPERATOR,
            action="KILL_SWITCH_ENGAGED",
            before={"kill_switch": False},
            after={"kill_switch": True},
        )

    with session_scope() as session:
        row = session.query(AuditLog).one()
        assert row.actor == "OPERATOR"
        assert verify_chain(session).valid is True


# ---------------------------------------------------------------------------
# DB-INV-1 — the reserve invariant
# ---------------------------------------------------------------------------


def add_policy_with_reserve(session, *, number: str, max_loss: str, reserve: str | None) -> None:
    policy = Policy(
        policy_number=number,
        correlation_id="c",
        underlying="SPY",
        structure="PUT_CREDIT_SPREAD",
        status="OPEN",
        max_loss=Decimal(max_loss),
        capital_reserve=Decimal(max_loss),
    )
    session.add(policy)
    session.flush()
    if reserve is not None:
        session.add(Reserve(policy_id=policy.id, amount=Decimal(reserve), status="HELD"))


def test_the_invariant_holds_on_an_empty_book() -> None:
    with session_scope() as session:
        result = check_reserve_invariant(session)
        assert result.holds is True
        assert result.held_reserves == Decimal("0")


def test_the_invariant_holds_when_every_policy_is_reserved() -> None:
    with session_scope() as session:
        add_policy_with_reserve(session, number="UW-1", max_loss="150.00", reserve="150.00")
        add_policy_with_reserve(session, number="UW-2", max_loss="320.50", reserve="320.50")

    with session_scope() as session:
        result = check_reserve_invariant(session)
        assert result.holds is True
        assert result.held_reserves == Decimal("470.50")


def test_an_unreserved_policy_breaks_the_invariant() -> None:
    """The drift that would make every capital limit above it decorative."""
    with session_scope() as session:
        add_policy_with_reserve(session, number="UW-1", max_loss="150.00", reserve="150.00")
        add_policy_with_reserve(session, number="UW-2", max_loss="200.00", reserve=None)

    with session_scope() as session:
        result = check_reserve_invariant(session)
        assert result.holds is False
        assert result.difference == Decimal("-200.00")
        assert "under-held" in result.detail


def test_a_reserve_left_held_after_settlement_breaks_the_invariant() -> None:
    with session_scope() as session:
        add_policy_with_reserve(session, number="UW-1", max_loss="150.00", reserve="150.00")

    with session_scope() as session:
        session.query(Policy).one().status = "SETTLED"  # reserve never released

    with session_scope() as session:
        result = check_reserve_invariant(session)
        assert result.holds is False
        assert result.difference == Decimal("150.00")
        assert "over-held" in result.detail


def test_a_released_reserve_stops_counting() -> None:
    with session_scope() as session:
        add_policy_with_reserve(session, number="UW-1", max_loss="150.00", reserve="150.00")

    with session_scope() as session:
        session.query(Policy).one().status = "SETTLED"
        reserve = session.query(Reserve).one()
        reserve.status = "RELEASED"
        reserve.released_at = NOW

    with session_scope() as session:
        assert check_reserve_invariant(session).holds is True


def test_a_closing_policy_still_needs_its_reserve() -> None:
    """CLOSING is still exposed: the position is not gone until it settles."""
    with session_scope() as session:
        add_policy_with_reserve(session, number="UW-1", max_loss="150.00", reserve="150.00")

    with session_scope() as session:
        session.query(Policy).one().status = "CLOSING"

    with session_scope() as session:
        assert check_reserve_invariant(session).holds is True

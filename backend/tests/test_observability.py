"""Observability — OPS-001 … OPS-010.

Two claims are under test here and both were broken before this module existed.

The first is that the application logs at all. Uvicorn configures its own
loggers and leaves the root logger alone, so every `log.info` and `log.error`
in this codebase went to a handler that did not exist — including
`log.error("forced MANAGE_ONLY")`, which is what the desk emits when it halts
itself. A halt nobody can see is a halt nobody responds to.

The second is that the numbers OPS-003 requires can actually be read. They are
derived from the ledger rather than from counters held in memory, so the test
that matters is that a metric agrees with the rows it claims to summarise.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from underwriter.db import create_all, reset_engine, session_scope
from underwriter.db.models import (
    Candidate,
    KernelDecision,
    Order,
    Policy,
    RiskCheck,
    SchedulerRun,
    SystemEvent,
    UnderwritingDecision,
)
from underwriter.obs import metrics
from underwriter.obs.logging import (
    JsonFormatter,
    configure_logging,
    correlation,
    current_correlation_id,
    set_correlation_id,
)

NOW = datetime(2026, 9, 5, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    create_all()
    yield
    reset_engine()


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """`configure_logging` clears the root handlers, and pytest wants them back."""
    root = logging.getLogger()
    saved = list(root.handlers)
    level = root.level
    yield
    root.handlers = saved
    root.setLevel(level)


def emit(record_args: dict[str, object] | None = None, **kwargs: object) -> dict[str, object]:
    """Format one record through the real formatter and read it back as JSON."""
    record = logging.LogRecord(
        name=str(kwargs.get("name", "underwriter.kernel.kernel")),
        level=int(kwargs.get("level", logging.INFO)),  # type: ignore[arg-type]
        pathname=__file__,
        lineno=1,
        msg=str(kwargs.get("msg", "verdict issued")),
        args=None,
        exc_info=kwargs.get("exc_info"),  # type: ignore[arg-type]
    )
    for key, value in (record_args or {}).items():
        setattr(record, key, value)

    return dict(json.loads(JsonFormatter().format(record)))


# -- OPS-001: every line is JSON with the same five fields -----------------


def test_every_log_line_carries_the_five_required_fields() -> None:
    payload = emit()

    assert set(payload) >= {"timestamp", "level", "component", "correlation_id", "event"}
    assert payload["level"] == "INFO"
    assert payload["component"] == "underwriter.kernel.kernel"
    assert payload["event"] == "verdict issued"
    # Parseable as an instant, not merely a string that looks like one.
    assert datetime.fromisoformat(str(payload["timestamp"])).tzinfo is not None


def test_extra_fields_pass_through_but_logging_internals_do_not() -> None:
    """A structured line is only useful if it can carry the event's own data."""
    payload = emit({"rule_id": "SK-006", "observed": "0.42"})

    assert payload["rule_id"] == "SK-006"
    assert payload["observed"] == "0.42"
    # LogRecord internals would drown the line in noise.
    assert "pathname" not in payload
    assert "levelno" not in payload
    assert "msg" not in payload


def test_non_serialisable_values_do_not_kill_the_log_line() -> None:
    """Logging must never be the thing that raises.

    A `Decimal` in `extra` is not JSON-serialisable, and money is a Decimal
    everywhere in this system (NFR-013), so this is the common case rather than
    an exotic one.
    """
    from decimal import Decimal

    payload = emit({"credit": Decimal("1.25"), "when": NOW})

    assert payload["credit"] == "1.25"
    assert "2026-09-05" in str(payload["when"])


# -- OPS-002: one correlation id per cycle or request ----------------------


def test_correlation_id_is_absent_until_something_sets_it() -> None:
    set_correlation_id(None)
    assert emit()["correlation_id"] is None


def test_correlation_scope_applies_to_lines_written_inside_it() -> None:
    set_correlation_id(None)

    with correlation("cyc_abc123"):
        assert emit()["correlation_id"] == "cyc_abc123"
        assert current_correlation_id() == "cyc_abc123"

    assert current_correlation_id() is None


def test_nested_scopes_restore_the_outer_id() -> None:
    """A cycle triggered by a request must not steal the request's id on exit."""
    with correlation("req_outer"):
        with correlation("cyc_inner"):
            assert current_correlation_id() == "cyc_inner"
        assert current_correlation_id() == "req_outer"


# -- OPS-010: stack traces to the log, never to the caller -----------------


def test_exceptions_are_logged_with_a_full_traceback() -> None:
    try:
        raise ValueError("nonce already consumed")
    except ValueError:
        import sys

        payload = emit(level=logging.ERROR, msg="execution refused", exc_info=sys.exc_info())

    exception = payload["exception"]
    assert isinstance(exception, dict)
    assert exception["type"] == "ValueError"
    assert exception["message"] == "nonce already consumed"
    assert "ValueError: nonce already consumed" in str(exception["traceback"])


def test_configure_logging_makes_application_lines_reach_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The regression that started all of this.

    Before `configure_logging` existed the root logger had no handler, so this
    line — the shape of the one the desk emits when it halts itself — was
    written to nowhere at all.
    """
    configure_logging("INFO")
    logging.getLogger("underwriter.cycle.bootstrap").error(
        "forced MANAGE_ONLY", extra={"reason": "reserve invariant broken"}
    )

    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)

    assert payload["level"] == "ERROR"
    assert payload["event"] == "forced MANAGE_ONLY"
    assert payload["reason"] == "reserve invariant broken"


def test_configure_logging_respects_the_configured_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("WARNING")
    log = logging.getLogger("underwriter.test")
    log.info("routine")
    log.warning("worth reading")

    out = capsys.readouterr().out
    assert "routine" not in out
    assert "worth reading" in out


def test_configure_logging_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    """Called twice, a line must appear once — not twice."""
    configure_logging("INFO")
    configure_logging("INFO")
    logging.getLogger("underwriter.test").info("once")

    assert capsys.readouterr().out.count('"event":"once"') == 1


# -- OPS-003 / OPS-004: counters read off the ledger -----------------------


def seed_book() -> None:
    """One rejected proposal, one approved, one settled policy, two cycles."""
    with session_scope() as session:
        session.add_all(
            [
                SchedulerRun(
                    job_name="underwrite",
                    status="NO_ACTION",
                    outcome="DECLINED",
                    started_at=NOW,
                    finished_at=NOW,
                    duration_ms=1200,
                ),
                SchedulerRun(
                    job_name="reconcile",
                    status="SUCCESS",
                    outcome="RECONCILED",
                    started_at=NOW,
                    finished_at=NOW,
                    duration_ms=400,
                ),
                Candidate(
                    correlation_id="c1",
                    underlying="SPY",
                    structure="PCS",
                    proposal_hash="h1",
                ),
                Candidate(
                    correlation_id="c1",
                    underlying="QQQ",
                    structure="PCS",
                    proposal_hash="h2",
                ),
                UnderwritingDecision(correlation_id="c1", action="WRITE", latency_ms=900),
                UnderwritingDecision(correlation_id="c2", action="DECLINE", latency_ms=1100),
                UnderwritingDecision(
                    correlation_id="c3", action="DECLINE", latency_ms=None, schema_valid=0
                ),
                Policy(
                    policy_number="UW-2026-0001",
                    correlation_id="c1",
                    underlying="SPY",
                    structure="PCS",
                    status="SETTLED",
                    settlement_reason="PROFIT_TARGET",
                ),
                SystemEvent(level="ERROR", component="mcp", event="get_all_positions"),
            ]
        )

        rejected = KernelDecision(
            correlation_id="c2",
            proposal_hash="h2",
            verdict="REJECT",
            nonce="n-reject",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=45),
        )
        approved = KernelDecision(
            correlation_id="c1",
            proposal_hash="h1",
            verdict="APPROVE",
            approved_contracts=2,
            nonce="n-approve",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=45),
            signature="sig",
        )
        session.add_all([rejected, approved])
        session.flush()

        session.add_all(
            [
                # One proposal that broke two limits at once.
                RiskCheck(
                    kernel_decision_id=rejected.id,
                    rule_id="SK-006",
                    rule_name="reserve headroom",
                    passed=0,
                    severity="HARD",
                ),
                RiskCheck(
                    kernel_decision_id=rejected.id,
                    rule_id="SK-014",
                    rule_name="liquidity",
                    passed=0,
                    severity="SOFT",
                ),
                RiskCheck(
                    kernel_decision_id=approved.id,
                    rule_id="SK-006",
                    rule_name="reserve headroom",
                    passed=1,
                    severity="HARD",
                ),
                Order(
                    client_order_id="uw-1",
                    kernel_decision_id=approved.id,
                    intent="ENTRY",
                    status="filled",
                ),
                Order(
                    client_order_id="uw-2",
                    kernel_decision_id=approved.id,
                    intent="ENTRY",
                    status="rejected",
                    error_code="40310000",
                ),
            ]
        )


def test_snapshot_counts_what_the_ledger_says() -> None:
    seed_book()
    counters = metrics.snapshot()["counters"]

    assert counters["cycles_total"] == {"NO_ACTION": 1, "SUCCESS": 1}
    assert counters["cycles_by_job"] == {"underwrite": 1, "reconcile": 1}
    assert counters["candidates_priced_total"] == 2
    assert counters["llm_calls_total"] == {"WRITE": 1, "DECLINE": 2}
    assert counters["kernel_verdicts_total"] == {"APPROVE": 1, "REJECT": 1}
    assert counters["kernel_rule_failures_total"] == {"SK-006": 1, "SK-014": 1}
    assert counters["orders_submitted_total"] == {"filled": 1, "rejected": 1}
    assert counters["policies_settled_total"] == {"PROFIT_TARGET": 1}
    assert counters["alpaca_errors_total"] == {"40310000": 1}
    assert counters["mcp_errors_total"] == {"get_all_positions": 1}


def test_latencies_ignore_the_rows_that_have_none() -> None:
    """A missing latency is not a zero, and averaging it in would be a lie."""
    seed_book()
    latencies = metrics.snapshot()["latencies"]

    assert latencies["llm_latency_ms"] == {
        "count": 2,
        "mean_ms": 1000.0,
        "min_ms": 900,
        "max_ms": 1100,
    }
    assert latencies["cycle_duration_ms"]["max_ms"] == 1200


def test_an_empty_book_reports_zeroes_rather_than_failing() -> None:
    """The state the dashboard is in for the first half hour after a deploy."""
    snapshot = metrics.snapshot()

    assert snapshot["counters"]["kernel_verdicts_total"] == {}
    assert snapshot["counters"]["candidates_priced_total"] == 0
    assert snapshot["latencies"]["llm_latency_ms"] == {
        "count": 0,
        "mean_ms": None,
        "min_ms": None,
        "max_ms": None,
    }


def test_schema_invalid_llm_responses_are_counted_separately() -> None:
    """FR-021 stores the malformed ones; a metric that hid them would flatter."""
    seed_book()
    assert metrics.snapshot()["llm_schema_invalid_total"] == 1


# -- OPS-008: the per-rule veto breakdown ----------------------------------


def test_veto_summary_reports_the_rate_and_the_rules_behind_it() -> None:
    seed_book()
    summary = metrics.veto_summary()

    assert summary["proposals_evaluated"] == 2
    assert summary["approved"] == 1
    assert summary["vetoed"] == 1
    assert summary["veto_rate"] == 0.5

    rules = {entry["rule_id"]: entry for entry in summary["by_rule"]}
    assert set(rules) == {"SK-006", "SK-014"}
    assert rules["SK-006"]["name"] == "reserve headroom"
    assert rules["SK-006"]["severity"] == "HARD"


def test_a_rule_that_never_failed_is_absent_rather_than_zero() -> None:
    """Twenty-six zeroes would bury the two lines that matter."""
    seed_book()
    listed = {entry["rule_id"] for entry in metrics.veto_summary()["by_rule"]}

    # SK-006 appears twice in risk_checks, once failing and once passing.
    assert "SK-006" in listed
    # Rules the Kernel evaluated but which never blocked anything stay out.
    assert "SK-023" not in listed


def test_failures_count_rules_and_proposals_blocked_counts_proposals() -> None:
    """One proposal breaking two limits died of two things, not one.

    Collapsing that to a single "rejected" tally would hide which limit is
    actually binding, which is the only question this endpoint exists to answer.
    """
    seed_book()
    rules = {entry["rule_id"]: entry for entry in metrics.veto_summary()["by_rule"]}

    assert rules["SK-006"]["failures"] == 1
    assert rules["SK-006"]["proposals_blocked"] == 1
    assert rules["SK-014"]["proposals_blocked"] == 1
    assert sum(entry["failures"] for entry in rules.values()) == 2


def test_veto_summary_survives_an_empty_book() -> None:
    summary = metrics.veto_summary()

    assert summary["proposals_evaluated"] == 0
    assert summary["veto_rate"] is None
    assert summary["by_rule"] == []


def test_the_limit_caps_the_rule_list() -> None:
    seed_book()
    assert len(metrics.veto_summary(limit=1)["by_rule"]) == 1

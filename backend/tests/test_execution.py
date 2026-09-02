"""The Execution Engine — TEST-050 … TEST-055, and FR-080's central claim.

The broker fake counts transmissions. That is the assertion that matters most
in this file: in every refusal case the count must still be zero. A test that
only checks the return value would pass even if the engine had already sent the
order and then decided against it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import SECRET, make_context, make_proposal
from underwriter.domain.proposal import Action
from underwriter.execution.engine import ExecutionEngine, ExecutionStatus
from underwriter.execution.order import (
    OrderConstructionError,
    build_entry_order,
    build_exit_order,
    entry_limit_price,
    exit_limit_price,
    simplify_ratios,
)
from underwriter.execution.ports import (
    BrokerOrder,
    BrokerPosition,
    OrderState,
    PermanentBrokerError,
    TransientBrokerError,
)
from underwriter.execution.ratelimit import TokenBucket
from underwriter.kernel import kernel
from underwriter.kernel.verdict import NonceRegistry, UnauthorizedExecution

NOW = datetime(2026, 9, 1, 15, 30, tzinfo=UTC)


class FakeBroker:
    """Counts every transmission, so a refusal can be proved silent."""

    def __init__(
        self,
        *,
        states: list[OrderState] | None = None,
        filled_qty: Decimal = Decimal("2"),
        ordered_qty: Decimal = Decimal("2"),
        submit_errors: list[Exception] | None = None,
        positions: list[BrokerPosition] | None = None,
        missing_after_submit: bool = False,
    ) -> None:
        self.states = states or [OrderState.FILLED]
        self.filled_qty = filled_qty
        self.ordered_qty = ordered_qty
        self.submit_errors = submit_errors or []
        self.positions = positions or []
        self.missing_after_submit = missing_after_submit

        self.transmitted: list[dict] = []
        self.cancelled: list[str] = []
        self.polls = 0
        self._last: BrokerOrder | None = None

    def _order(self, state: OrderState, client_order_id: str) -> BrokerOrder:
        # Anything past "working" carries whatever filled before it got there:
        # a partial fill in the wild is usually a cancel that caught some of it.
        settled = state not in {OrderState.NEW, OrderState.ACCEPTED}
        return BrokerOrder(
            broker_order_id="brk-1",
            client_order_id=client_order_id,
            state=state,
            filled_qty=self.filled_qty if settled else Decimal("0"),
            ordered_qty=self.ordered_qty,
            filled_avg_price=Decimal("0.50") if settled else None,
            raw={"status": str(state)},
        )

    def submit_order(self, payload: dict) -> BrokerOrder:
        if self.submit_errors:
            raise self.submit_errors.pop(0)
        self.transmitted.append(payload)
        self._last = self._order(self.states[0], payload["client_order_id"])
        return self._last

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        self.polls += 1
        if self.missing_after_submit:
            return None
        state = self.states[min(self.polls, len(self.states) - 1)]
        self._last = self._order(state, client_order_id)
        return self._last

    def cancel_order(self, broker_order_id: str) -> None:
        self.cancelled.append(broker_order_id)

    def list_positions(self) -> list[BrokerPosition]:
        return self.positions


def approved(proposal=None, contracts: int = 2):  # type: ignore[no-untyped-def]
    proposal = proposal or make_proposal()
    verdict = kernel.evaluate(
        proposal, requested_contracts=contracts, context=make_context(), secret=SECRET
    )
    assert verdict.approved, kernel.explain(verdict)
    return proposal, verdict


def engine(broker: FakeBroker, **kwargs) -> ExecutionEngine:  # type: ignore[no-untyped-def]
    """No real sleeping, and a clock the test drives."""
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("rate_limiter", TokenBucket(sleep=lambda _s: None))
    return ExecutionEngine(broker, secret=SECRET, **kwargs)


# ---------------------------------------------------------------------------
# FR-080 / TEST-030 — nothing transmits without a valid verdict
# ---------------------------------------------------------------------------


def test_080_no_verdict_transmits_nothing() -> None:
    broker = FakeBroker()
    proposal = make_proposal()

    with pytest.raises(UnauthorizedExecution):
        engine(broker).execute(proposal, None, now=NOW)

    assert broker.transmitted == [], "an order reached the broker without a verdict"


def test_080_a_rejection_transmits_nothing() -> None:
    broker = FakeBroker()
    proposal = make_proposal(dte=0)  # trips SK-011
    verdict = kernel.evaluate(
        proposal, requested_contracts=1, context=make_context(), secret=SECRET
    )
    assert not verdict.approved

    with pytest.raises(UnauthorizedExecution):
        engine(broker).execute(proposal, verdict, now=NOW)

    assert broker.transmitted == []


def test_080_a_verdict_for_another_proposal_transmits_nothing() -> None:
    """TEST-032, at the point where it would actually matter."""
    broker = FakeBroker()
    _, verdict = approved()
    other = make_proposal(short_strike=545, long_strike=540)

    with pytest.raises(UnauthorizedExecution, match="does not match"):
        engine(broker).execute(other, verdict, now=NOW)

    assert broker.transmitted == []


def test_080_an_expired_verdict_transmits_nothing() -> None:
    broker = FakeBroker()
    proposal, verdict = approved()

    with pytest.raises(UnauthorizedExecution, match="expired"):
        engine(broker).execute(proposal, verdict, now=verdict.expires_at + timedelta(seconds=1))

    assert broker.transmitted == []


def test_080_a_replayed_verdict_transmits_only_once() -> None:
    """TEST-034 through the engine: the second attempt reaches no broker."""
    broker = FakeBroker()
    proposal, verdict = approved()
    nonces = NonceRegistry()
    eng = engine(broker, nonces=nonces)

    eng.execute(proposal, verdict, now=NOW)
    assert len(broker.transmitted) == 1

    with pytest.raises(UnauthorizedExecution, match="already used"):
        eng.execute(proposal, verdict, now=NOW)

    assert len(broker.transmitted) == 1, "a replayed verdict transmitted a second order"


# ---------------------------------------------------------------------------
# TEST-050 — the mleg payload
# ---------------------------------------------------------------------------


def test_050_the_entry_payload_matches_the_documented_schema() -> None:
    proposal = make_proposal()
    payload = build_entry_order(proposal, contracts=3)

    assert payload["order_class"] == "mleg"  # ALP-010
    assert payload["type"] == "limit"  # ALP-015, FR-082
    assert payload["time_in_force"] == "day"
    assert payload["qty"] == "3"
    assert len(payload["legs"]) == 2

    short, long = payload["legs"]
    assert short["side"] == "sell"
    assert short["position_intent"] == "sell_to_open"  # ALP-012
    assert long["side"] == "buy"
    assert long["position_intent"] == "buy_to_open"
    assert {leg["ratio_qty"] for leg in payload["legs"]} == {"1"}


def test_050_ratio_quantities_are_reduced_to_simplest_form() -> None:
    """ALP-011: GCD across legs must be 1."""
    assert simplify_ratios([2, 4]) == [1, 2]
    assert simplify_ratios([3, 3]) == [1, 1]
    assert simplify_ratios([6, 9, 15]) == [2, 3, 5]


def test_050_non_positive_ratios_are_refused() -> None:
    with pytest.raises(OrderConstructionError):
        simplify_ratios([0, 1])


def test_050_an_uncovered_short_cannot_be_built() -> None:
    """ALP-014, and SK-004 a second time. Alpaca would reject it anyway."""
    naked = make_proposal(legs=(make_proposal().legs[0],))
    with pytest.raises(OrderConstructionError, match="covered"):
        build_entry_order(naked, contracts=1)


def test_050_an_exit_inverts_every_leg() -> None:
    """ALP-016. Reusing the opening sides would double the position, not close it."""
    proposal = make_proposal(action=Action.CLOSE)
    payload = build_exit_order(proposal, contracts=2, target_debit=Decimal("0.25"))

    short, long = payload["legs"]
    assert short["side"] == "buy"  # the short leg is bought back
    assert short["position_intent"] == "buy_to_close"
    assert long["side"] == "sell"  # the long leg is sold
    assert long["position_intent"] == "sell_to_close"


def test_050_an_entry_cannot_be_built_from_a_closing_proposal() -> None:
    with pytest.raises(OrderConstructionError, match="cannot build an entry"):
        build_entry_order(make_proposal(action=Action.CLOSE), contracts=1)


def test_050_zero_contracts_is_refused() -> None:
    with pytest.raises(OrderConstructionError, match="at least 1"):
        build_entry_order(make_proposal(), contracts=0)


# ---------------------------------------------------------------------------
# FR-082 — limit prices, and the direction they walk
# ---------------------------------------------------------------------------


def test_082_an_entry_walks_toward_less_credit() -> None:
    """Conservative: accepting less can only shrink the payoff, never the risk."""
    assert entry_limit_price(Decimal("0.50"), 0) == Decimal("0.50")
    assert entry_limit_price(Decimal("0.50"), 1) == Decimal("0.49")
    assert entry_limit_price(Decimal("0.50"), 3) == Decimal("0.47")


def test_082_an_exit_walks_toward_paying_more() -> None:
    """Asymmetric on purpose: getting out is worth overpaying for."""
    assert exit_limit_price(Decimal("0.25"), 0) == Decimal("0.25")
    assert exit_limit_price(Decimal("0.25"), 3) == Decimal("0.28")


def test_082_a_walk_that_reaches_zero_credit_is_refused() -> None:
    with pytest.raises(OrderConstructionError, match="non-positive"):
        entry_limit_price(Decimal("0.02"), 3)


def test_082_a_step_beyond_the_maximum_is_refused() -> None:
    with pytest.raises(OrderConstructionError, match="outside"):
        entry_limit_price(Decimal("0.50"), 4)


# ---------------------------------------------------------------------------
# TEST-051 — idempotency
# ---------------------------------------------------------------------------


def test_051_the_client_order_id_is_deterministic_from_the_proposal() -> None:
    proposal = make_proposal()
    first = build_entry_order(proposal, contracts=1)["client_order_id"]
    second = build_entry_order(proposal, contracts=5)["client_order_id"]

    assert first == second, "the id must not depend on size, only on the proposal"
    other = build_entry_order(make_proposal(short_strike=545), contracts=1)
    assert other["client_order_id"] != first


# ---------------------------------------------------------------------------
# TEST-052 — a 200 is not a fill
# ---------------------------------------------------------------------------


def test_052_an_accepted_order_is_polled_to_terminal() -> None:
    broker = FakeBroker(states=[OrderState.NEW, OrderState.ACCEPTED, OrderState.FILLED])
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)

    assert result.status is ExecutionStatus.FILLED
    assert broker.polls >= 2, "the engine treated a non-terminal state as terminal"


def test_052_a_rejected_order_is_reported_as_rejected() -> None:
    broker = FakeBroker(states=[OrderState.REJECTED])
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)
    assert result.status is ExecutionStatus.REJECTED


# ---------------------------------------------------------------------------
# TEST-053 — timeout, cancel, confirm
# ---------------------------------------------------------------------------


def test_053_a_stuck_order_is_cancelled_and_confirmed() -> None:
    """FR-085: no phantom policy is left behind."""
    broker = FakeBroker(states=[OrderState.NEW], filled_qty=Decimal("0"))
    proposal, verdict = approved()

    ticks = iter([0.0, 0.0, 200.0, 200.0, 200.0])
    result = engine(broker, timeout_sec=120, clock=lambda: next(ticks)).execute(
        proposal, verdict, now=NOW
    )

    assert result.status is ExecutionStatus.TIMED_OUT
    assert broker.cancelled == ["brk-1"]
    assert "cancelled and confirmed" in result.detail


def test_053_a_cancel_that_races_a_fill_reports_the_fill() -> None:
    """Whatever filled before the cancel landed is real and must be recorded."""
    broker = FakeBroker(
        states=[OrderState.NEW, OrderState.FILLED],
        filled_qty=Decimal("2"),
        ordered_qty=Decimal("2"),
    )
    proposal, verdict = approved()

    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0])
    result = engine(broker, timeout_sec=120, clock=lambda: next(ticks)).execute(
        proposal, verdict, now=NOW
    )

    assert result.status is ExecutionStatus.FILLED
    assert result.contracts_filled == Decimal("2")


def test_053_an_order_that_vanishes_mid_poll_is_a_failure_not_a_fill() -> None:
    broker = FakeBroker(states=[OrderState.NEW], missing_after_submit=True)
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)
    assert result.status is ExecutionStatus.FAILED
    assert "disappeared" in result.detail


# ---------------------------------------------------------------------------
# TEST-054 — partial fills are LEG_RISK
# ---------------------------------------------------------------------------


def test_054_a_partial_fill_is_flagged_for_escalation() -> None:
    """F-13: one leg on and one leg off is uncovered short exposure."""
    broker = FakeBroker(
        states=[OrderState.CANCELED],
        filled_qty=Decimal("1"),
        ordered_qty=Decimal("3"),
    )
    proposal, verdict = approved(contracts=3)

    result = engine(broker).execute(proposal, verdict, now=NOW)

    assert result.status is ExecutionStatus.PARTIAL
    assert result.needs_escalation is True
    assert "LEG_RISK" in result.detail


def test_054_a_complete_fill_is_not_partial() -> None:
    broker = FakeBroker(states=[OrderState.FILLED], filled_qty=Decimal("2"))
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)
    assert result.status is ExecutionStatus.FILLED
    assert result.needs_escalation is False


# ---------------------------------------------------------------------------
# TEST-055 — retry policy
# ---------------------------------------------------------------------------


def test_055_a_transient_error_is_retried_with_backoff() -> None:
    broker = FakeBroker(
        states=[OrderState.FILLED],
        submit_errors=[TransientBrokerError("429 rate limited")],
    )
    proposal, verdict = approved()

    waits: list[float] = []
    result = engine(broker, sleep=waits.append).execute(proposal, verdict, now=NOW)

    assert result.status is ExecutionStatus.FILLED
    assert result.attempts == 2
    assert waits and waits[0] > 0, "a retry must back off"


def test_055_a_validation_error_is_never_retried() -> None:
    """FR-087: a 422 retried is a 422 again, more slowly."""
    broker = FakeBroker(submit_errors=[PermanentBrokerError("422 invalid leg")])
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)

    assert result.status is ExecutionStatus.REJECTED
    assert result.attempts == 1
    assert broker.transmitted == []


def test_055_exhausted_retries_ask_rather_than_resubmit() -> None:
    """F-15: after an ambiguous failure, never resubmit — look it up."""
    broker = FakeBroker(
        states=[OrderState.FILLED],
        submit_errors=[TransientBrokerError("timeout")] * 3,
    )
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)

    assert broker.transmitted == [], "the engine resubmitted after an ambiguous failure"
    assert broker.polls >= 1, "the engine did not look up the order's true state"
    assert result.status in {ExecutionStatus.FILLED, ExecutionStatus.FAILED}


def test_055_an_ambiguous_failure_with_no_order_reports_failure() -> None:
    broker = FakeBroker(
        submit_errors=[TransientBrokerError("timeout")] * 3, missing_after_submit=True
    )
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)
    assert result.status is ExecutionStatus.FAILED
    assert "no order exists under this id" in result.detail


# ---------------------------------------------------------------------------
# FR-089 — reconciliation after every attempt
# ---------------------------------------------------------------------------


def test_089_broker_positions_are_reported_as_divergences() -> None:
    broker = FakeBroker(
        states=[OrderState.FILLED],
        positions=[BrokerPosition(symbol="SPY260918P00550000", qty=Decimal("-2"))],
    )
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)
    assert result.divergences
    assert "SPY260918P00550000" in result.divergences[0]


def test_089_a_reconciliation_failure_is_recorded_not_raised() -> None:
    class BrokenPositions(FakeBroker):
        def list_positions(self) -> list[BrokerPosition]:
            raise RuntimeError("positions endpoint down")

    broker = BrokenPositions(states=[OrderState.FILLED])
    proposal, verdict = approved()

    result = engine(broker).execute(proposal, verdict, now=NOW)
    assert result.status is ExecutionStatus.FILLED
    assert "reconciliation failed" in result.divergences[0]


# ---------------------------------------------------------------------------
# FR-088 — the token bucket
# ---------------------------------------------------------------------------


def test_088_the_bucket_paces_below_the_documented_ceiling() -> None:
    """ALP-023 documents 200 rpm. Pacing below it is the point."""
    from underwriter.execution.ratelimit import TRADING_RPM

    assert TRADING_RPM < 200


def test_088_an_empty_bucket_waits() -> None:
    ticks = iter([0.0] * 50)
    waits: list[float] = []
    bucket = TokenBucket(rate_per_minute=60, burst=2, clock=lambda: next(ticks), sleep=waits.append)

    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() > 0.0, "a third call against a burst of 2 must wait"
    assert waits


def test_088_the_bucket_refills_over_time() -> None:
    # Read once at construction, once by acquire(), once by available().
    ticks = iter([0.0, 0.0, 60.0])
    bucket = TokenBucket(
        rate_per_minute=60, burst=1, clock=lambda: next(ticks), sleep=lambda _: None
    )

    bucket.acquire()
    assert bucket.available >= 0.9


def test_088_a_request_larger_than_the_bucket_is_refused() -> None:
    bucket = TokenBucket(rate_per_minute=60, burst=2)
    with pytest.raises(ValueError, match="cannot acquire"):
        bucket.acquire(5)


def test_088_an_impossible_rate_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        TokenBucket(rate_per_minute=0)


# ---------------------------------------------------------------------------
# Sizing comes from the verdict, never from the model
# ---------------------------------------------------------------------------


def test_the_order_is_sized_by_the_verdict_not_the_request() -> None:
    """FR-046, at the only point where it can still be got wrong."""
    broker = FakeBroker(states=[OrderState.FILLED])
    proposal = make_proposal()
    verdict = kernel.evaluate(
        proposal, requested_contracts=500, context=make_context(), secret=SECRET
    )
    assert verdict.approved_contracts < 500

    engine(broker).execute(proposal, verdict, now=NOW)
    assert broker.transmitted[0]["qty"] == str(verdict.approved_contracts)


def test_a_closing_order_requires_a_target_debit() -> None:
    broker = FakeBroker()
    proposal = make_proposal(action=Action.CLOSE)
    verdict = kernel.evaluate(
        proposal, requested_contracts=1, context=make_context(), secret=SECRET
    )

    with pytest.raises(ValueError, match="target debit"):
        engine(broker).execute(proposal, verdict, now=NOW)

    assert broker.transmitted == []


# ---------------------------------------------------------------------------
# The Alpaca adapter — the parts reachable without a network
# ---------------------------------------------------------------------------


def test_the_broker_refuses_to_exist_without_trading_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing here degrades to read-only. No credentials means no engine."""
    from underwriter.execution.alpaca_broker import AlpacaBroker, TradingCredentialsError

    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    with pytest.raises(TradingCredentialsError, match="required to transmit"):
        AlpacaBroker()


def test_the_broker_refuses_to_exist_outside_paper_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-004 at the last possible moment before an order could be sent."""
    from underwriter.execution.alpaca_broker import AlpacaBroker, TradingCredentialsError

    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "false")

    with pytest.raises(TradingCredentialsError, match="paper-only"):
        AlpacaBroker()


def test_the_payload_translates_to_the_sdk_request_faithfully() -> None:
    """The dict is the audited artifact; this is only its transport shape."""
    from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent

    from underwriter.execution.alpaca_broker import AlpacaBroker

    payload = build_entry_order(make_proposal(), contracts=3)
    request = AlpacaBroker._to_request(payload)

    assert request.qty == 3
    assert request.order_class is OrderClass.MLEG
    assert float(request.limit_price) == float(payload["limit_price"])
    assert request.client_order_id == payload["client_order_id"]
    assert len(request.legs) == 2
    assert request.legs[0].side is OrderSide.SELL
    assert request.legs[0].position_intent is PositionIntent.SELL_TO_OPEN


@pytest.mark.parametrize(
    ("status", "expected_transient"),
    [(429, True), (503, True), (500, True), (422, False), (403, False), (404, False)],
)
def test_broker_errors_are_classified_by_whether_retrying_could_help(
    status: int, expected_transient: bool
) -> None:
    """FR-087. A 422 retried is a 422 again; a 429 abandoned is a cycle lost."""
    from underwriter.execution.alpaca_broker import AlpacaBroker

    error = Exception("boom")
    error.status_code = status  # type: ignore[attr-defined]

    classified = AlpacaBroker._classify(error)
    assert isinstance(classified, TransientBrokerError) is expected_transient


def test_a_network_fault_with_no_status_is_treated_as_transient() -> None:
    from underwriter.execution.alpaca_broker import AlpacaBroker

    assert isinstance(
        AlpacaBroker._classify(Exception("connection reset by peer")), TransientBrokerError
    )
    assert isinstance(AlpacaBroker._classify(Exception("malformed leg")), PermanentBrokerError)


def test_an_unknown_order_status_is_never_treated_as_terminal() -> None:
    """FR-084: guessing terminal would end the poll and report an unconfirmed fill."""
    from underwriter.execution.alpaca_broker import AlpacaBroker

    class Raw:
        id = "brk-9"
        client_order_id = "uw_x"
        status = "some_new_status_alpaca_added"
        filled_qty = 0
        qty = 2
        filled_avg_price = None
        submitted_at = None

    order = AlpacaBroker._to_order(Raw())
    assert order.state is OrderState.NEW
    assert order.state.is_terminal is False


# ---------------------------------------------------------------------------
# ALP-007 — the pre-flight, and what it is allowed to do
# ---------------------------------------------------------------------------


class Preflight:
    """Stands in for the CLI check with a fixed answer."""

    def __init__(self, *, ok: bool, skipped: bool = False) -> None:
        from underwriter.cli import PreflightResult

        self.result = PreflightResult(ok=ok, skipped=skipped, detail="fixture")
        self.payloads: list[dict] = []

    def __call__(self, payload: dict):  # type: ignore[no-untyped-def]
        self.payloads.append(payload)
        return self.result


def test_007_a_failed_preflight_transmits_nothing() -> None:
    """ALP-007 is a SHOULD to run and a MUST to respect."""
    broker = FakeBroker(states=[OrderState.FILLED])
    proposal, verdict = approved()

    result = engine(broker, preflight=Preflight(ok=False)).execute(proposal, verdict, now=NOW)

    assert result.status is ExecutionStatus.REJECTED
    assert broker.transmitted == [], "an order was sent after the pre-flight failed"
    assert "pre-flight failed" in result.detail


def test_007_a_passing_preflight_lets_the_order_through() -> None:
    broker = FakeBroker(states=[OrderState.FILLED])
    proposal, verdict = approved()
    check = Preflight(ok=True)

    result = engine(broker, preflight=check).execute(proposal, verdict, now=NOW)

    assert result.status is ExecutionStatus.FILLED
    assert len(broker.transmitted) == 1
    # It sees exactly what would be sent, not a reconstruction of it.
    assert check.payloads[0] == broker.transmitted[0]


def test_007_a_skipped_preflight_never_blocks_a_cycle() -> None:
    """An absent optional tool is not a reason to refuse to trade."""
    broker = FakeBroker(states=[OrderState.FILLED])
    proposal, verdict = approved()

    result = engine(broker, preflight=Preflight(ok=True, skipped=True)).execute(
        proposal, verdict, now=NOW
    )
    assert result.status is ExecutionStatus.FILLED


def test_007_the_preflight_runs_after_authorisation_not_before() -> None:
    """An unauthorised order must not even reach the pre-flight."""
    check = Preflight(ok=True)
    broker = FakeBroker()

    with pytest.raises(UnauthorizedExecution):
        engine(broker, preflight=check).execute(make_proposal(), None, now=NOW)

    assert check.payloads == []
    assert broker.transmitted == []


def test_007_the_comparison_catches_a_mutated_leg() -> None:
    """The point of a second implementation: disagreement is the signal."""
    from underwriter.cli.preflight import _compare

    payload = build_entry_order(make_proposal(), contracts=2)
    rendered = {**payload, "legs": [dict(leg) for leg in payload["legs"]]}
    rendered["legs"][0]["position_intent"] = "buy_to_open"

    differences = _compare(payload, rendered)
    assert differences
    assert "position_intent" in differences[0]


def test_007_the_comparison_ignores_fields_the_cli_adds_of_its_own() -> None:
    from underwriter.cli.preflight import _compare

    payload = build_entry_order(make_proposal(), contracts=2)
    rendered = {**payload, "advanced_instructions": {}}
    assert _compare(payload, rendered) == ()


def test_007_a_leg_count_mismatch_is_reported_without_zipping() -> None:
    from underwriter.cli.preflight import _compare

    payload = build_entry_order(make_proposal(), contracts=1)
    rendered = {**payload, "legs": payload["legs"][:1]}

    differences = _compare(payload, rendered)
    assert differences == ("legs: ours=2 cli=1",)


# ---------------------------------------------------------------------------
# ALP-007 — the value check, which the CLI does not do for us
# ---------------------------------------------------------------------------


def test_007_a_well_formed_order_has_no_value_problems() -> None:
    from underwriter.cli import check_values

    assert check_values(build_entry_order(make_proposal(), contracts=2)) == ()


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ({"order_class": "simple"}, "ALP-010"),
        ({"type": "market"}, "FR-082"),
        ({"time_in_force": "fok"}, "ALP-015"),
        ({"qty": "0"}, "whole positive"),
        ({"qty": "two"}, "not an integer"),
    ],
)
def test_007_an_illegal_top_level_value_is_caught(mutate: dict[str, str], expected: str) -> None:
    """The CLI renders these back unchanged, so this check is the only one."""
    from underwriter.cli import check_values

    payload = {**build_entry_order(make_proposal(), contracts=1), **mutate}
    problems = check_values(payload)

    assert problems
    assert any(expected in problem for problem in problems)


def test_007_an_illegal_position_intent_is_caught() -> None:
    """Tested against the real CLI: it renders this back unchanged."""
    from underwriter.cli import check_values

    payload = build_entry_order(make_proposal(), contracts=1)
    payload["legs"][0]["position_intent"] = "not_a_real_intent"

    problems = check_values(payload)
    assert any("ALP-012" in problem for problem in problems)


def test_007_an_illegal_side_or_missing_symbol_is_caught() -> None:
    from underwriter.cli import check_values

    payload = build_entry_order(make_proposal(), contracts=1)
    payload["legs"][0]["side"] = "hold"
    payload["legs"][1]["symbol"] = "  "

    problems = check_values(payload)
    assert any("side" in problem for problem in problems)
    assert any("no symbol" in problem for problem in problems)


def test_007_a_single_leg_order_is_caught_as_uncovered() -> None:
    from underwriter.cli import check_values

    payload = build_entry_order(make_proposal(), contracts=1)
    payload["legs"] = payload["legs"][:1]

    assert any("ALP-014" in problem for problem in check_values(payload))


def test_007_the_value_check_runs_even_with_no_cli_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An illegal enum is worth catching whether or not the binary is here."""
    from underwriter.cli import preflight, validate_order

    monkeypatch.setattr(preflight, "is_available", lambda: False)

    payload = build_entry_order(make_proposal(), contracts=1)
    payload["legs"][0]["position_intent"] = "nonsense"

    result = validate_order(payload)
    assert result.ok is False
    assert result.skipped is False
    assert result.blocks_execution is True


def test_007_a_legal_order_skips_cleanly_when_the_cli_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from underwriter.cli import preflight, validate_order

    monkeypatch.setattr(preflight, "is_available", lambda: False)

    result = validate_order(build_entry_order(make_proposal(), contracts=1))
    assert result.ok is True
    assert result.skipped is True
    assert result.blocks_execution is False

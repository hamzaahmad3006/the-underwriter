"""The guarded execution path — FR-080 … FR-089.

Every order the system ever transmits passes through `execute()`, and the first
thing it does is verify a signature. There is no second entry point, no
`force=True`, and no operator override: SEC-012 puts a human command through the
same pipeline as a model's, and TD-11 records that the absence of an override
*is* the demo.

The order of operations matters and is not negotiable:

1. **Authorise or raise.** `UnauthorizedExecution` before anything is built.
2. **Build**, from the proposal and the verdict's approved size only.
3. **Submit**, retrying transient faults with backoff and jitter, never 4xx.
4. **Poll to terminal.** An HTTP 200 is not a fill (FR-084).
5. **On timeout, cancel** and re-poll to confirm (FR-085).
6. **Detect partial fills** and flag `LEG_RISK` (FR-086).
7. **Reconcile** against the broker's own positions (FR-089).

Step 4 is the one that looks like overhead and is not. A spread that reports
`new` and never fills, treated as filled, becomes a policy the book thinks it
holds and the broker has never heard of.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from underwriter.domain.proposal import Action, UnderwritingProposal
from underwriter.execution.order import build_entry_order, build_exit_order
from underwriter.execution.ports import (
    BrokerOrder,
    BrokerPort,
    BrokerPosition,
    OrderState,
    PermanentBrokerError,
    TransientBrokerError,
)
from underwriter.execution.ratelimit import TokenBucket
from underwriter.kernel.verdict import (
    KernelVerdict,
    NonceRegistry,
    authorize,
)

ORDER_TIMEOUT_SEC = 120  # FR-084
POLL_INTERVAL_SEC = 2.0
MAX_RETRIES = 3  # FR-087
BASE_BACKOFF_SEC = 0.5


class ExecutionStatus(StrEnum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"  # F-13: LEG_RISK, escalate immediately
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What happened, in enough detail to reconstruct it from the ledger."""

    status: ExecutionStatus
    client_order_id: str
    broker_order_id: str | None = None
    contracts_ordered: int = 0
    contracts_filled: Decimal = Decimal("0")
    filled_avg_price: Decimal | None = None
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    detail: str = ""
    divergences: tuple[str, ...] = ()
    as_of: datetime = datetime(1970, 1, 1, tzinfo=UTC)

    @property
    def needs_escalation(self) -> bool:
        """A partial fill or a divergence is the Claims Desk's problem now."""
        return self.status is ExecutionStatus.PARTIAL or bool(self.divergences)


class ExecutionEngine:
    """Turns a signed verdict into an order, or refuses.

    `secret` is the Kernel signing secret, used only to *verify*. This engine
    cannot mint a verdict — it has no path to `mint()` — so possession of the
    secret here buys the ability to check a signature and nothing else.
    """

    def __init__(
        self,
        broker: BrokerPort,
        *,
        secret: str,
        nonces: NonceRegistry | None = None,
        timeout_sec: int = ORDER_TIMEOUT_SEC,
        poll_interval_sec: float = POLL_INTERVAL_SEC,
        max_retries: int = MAX_RETRIES,
        rate_limiter: TokenBucket | None = None,
        preflight: Any = None,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
    ) -> None:
        self._broker = broker
        self._secret = secret
        self._nonces = nonces or NonceRegistry()
        self._timeout_sec = timeout_sec
        self._poll_interval = poll_interval_sec
        self._max_retries = max_retries
        self._bucket = rate_limiter or TokenBucket()
        # ALP-007. Optional to run, mandatory to respect: a pre-flight that
        # fails stops the order, and one that is absent does not.
        self._preflight = preflight
        self._sleep = sleep
        self._clock = clock

    # -- the single entry point ------------------------------------------

    def execute(
        self,
        proposal: UnderwritingProposal,
        verdict: KernelVerdict | None,
        *,
        now: datetime | None = None,
        target_debit: Decimal | None = None,
        price_step: int = 0,
    ) -> ExecutionResult:
        """Transmit an order, or raise `UnauthorizedExecution`.

        The authorisation check runs before the payload is even constructed.
        Building first and checking second would leave a window where a
        malformed-but-signed request had already touched the broker client.
        """
        as_of = now or datetime.now(UTC)

        # FR-080 / TEST-030. Nothing below this line runs without a verdict.
        contracts = authorize(
            verdict,
            proposal_hash=proposal.proposal_hash,
            secret=self._secret,
            now=as_of,
            nonces=self._nonces,
        )

        if proposal.action is Action.CLOSE:
            if target_debit is None:
                raise ValueError("a closing order needs a target debit")
            payload = build_exit_order(
                proposal, contracts=contracts, target_debit=target_debit, step=price_step
            )
        else:
            payload = build_entry_order(proposal, contracts=contracts, step=price_step)

        if self._preflight is not None:
            result = self._preflight(payload)
            if getattr(result, "blocks_execution", False):
                return ExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    client_order_id=str(payload["client_order_id"]),
                    contracts_ordered=contracts,
                    request=payload,
                    detail=f"pre-flight failed, nothing transmitted: {result.detail}",
                    as_of=as_of,
                )

        return self._transmit(payload, contracts, as_of)

    # -- submission ------------------------------------------------------

    def _transmit(
        self, payload: dict[str, Any], contracts: int, as_of: datetime
    ) -> ExecutionResult:
        client_order_id = str(payload["client_order_id"])
        attempts = 0
        order: BrokerOrder | None = None

        for attempt in range(1, self._max_retries + 1):
            attempts = attempt
            self._bucket.acquire()
            try:
                order = self._broker.submit_order(payload)
                break
            except PermanentBrokerError as exc:
                # FR-087: a validation error retried is a validation error twice.
                return ExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    client_order_id=client_order_id,
                    contracts_ordered=contracts,
                    request=payload,
                    attempts=attempt,
                    detail=f"broker rejected the order: {exc}",
                    as_of=as_of,
                )
            except TransientBrokerError as exc:
                if attempt == self._max_retries:
                    # F-15: the state is genuinely unknown. Do NOT resubmit —
                    # poll by client_order_id to find out what actually
                    # happened before anything else touches this policy.
                    return self._resolve_unknown(
                        client_order_id, payload, contracts, attempt, str(exc), as_of
                    )
                self._sleep(self._backoff(attempt))

        if order is None:  # pragma: no cover - defensive
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                client_order_id=client_order_id,
                contracts_ordered=contracts,
                request=payload,
                attempts=attempts,
                detail="submission produced no order and no error",
                as_of=as_of,
            )

        return self._await_terminal(order, payload, contracts, attempts, as_of)

    def _backoff(self, attempt: int) -> float:
        """Exponential with jitter (FR-087).

        Jitter matters more than the exponent here: without it, three cycles
        that all hit a 429 would retry in lockstep and hit it again together.
        """
        jitter: float = random.random()  # noqa: S311 - pacing, not cryptography
        return BASE_BACKOFF_SEC * (2.0 ** (attempt - 1)) * (1.0 + jitter)

    # -- polling ---------------------------------------------------------

    def _await_terminal(
        self,
        order: BrokerOrder,
        payload: dict[str, Any],
        contracts: int,
        attempts: int,
        as_of: datetime,
    ) -> ExecutionResult:
        """FR-084: poll until terminal, or time out and cancel."""
        deadline = self._clock() + self._timeout_sec
        current = order

        while not current.state.is_terminal:
            if self._clock() >= deadline:
                return self._cancel_and_confirm(current, payload, contracts, attempts, as_of)

            self._sleep(self._poll_interval)
            self._bucket.acquire()
            polled = self._broker.get_order_by_client_id(current.client_order_id)
            if polled is None:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    client_order_id=current.client_order_id,
                    broker_order_id=current.broker_order_id,
                    contracts_ordered=contracts,
                    request=payload,
                    attempts=attempts,
                    detail="the order disappeared from the broker mid-poll",
                    as_of=as_of,
                )
            current = polled

        return self._finalise(current, payload, contracts, attempts, as_of)

    def _cancel_and_confirm(
        self,
        order: BrokerOrder,
        payload: dict[str, Any],
        contracts: int,
        attempts: int,
        as_of: datetime,
    ) -> ExecutionResult:
        """FR-085: cancel, then re-poll to confirm it actually cancelled."""
        self._bucket.acquire()
        self._broker.cancel_order(order.broker_order_id)

        self._bucket.acquire()
        confirmed = self._broker.get_order_by_client_id(order.client_order_id) or order

        # A cancel that races a fill still leaves a position. Whatever filled
        # before the cancel landed is real and has to be reported as such.
        if confirmed.filled_qty > 0:
            return self._finalise(confirmed, payload, contracts, attempts, as_of)

        return ExecutionResult(
            status=ExecutionStatus.TIMED_OUT,
            client_order_id=order.client_order_id,
            broker_order_id=order.broker_order_id,
            contracts_ordered=contracts,
            request=payload,
            response=confirmed.raw,
            attempts=attempts,
            detail=(
                f"no terminal state within {self._timeout_sec}s; cancelled and "
                f"confirmed as {confirmed.state}"
            ),
            divergences=self._reconcile(),
            as_of=as_of,
        )

    def _resolve_unknown(
        self,
        client_order_id: str,
        payload: dict[str, Any],
        contracts: int,
        attempts: int,
        error: str,
        as_of: datetime,
    ) -> ExecutionResult:
        """F-15: a timeout mid-submission. Ask, never resubmit.

        The deterministic client_order_id is what makes this answerable: if the
        order did land, it is findable under the same id, and resubmitting
        would either collide or double the position.
        """
        try:
            self._bucket.acquire()
            existing = self._broker.get_order_by_client_id(client_order_id)
        except Exception as exc:
            existing = None
            error = f"{error}; state lookup also failed: {exc}"

        if existing is not None:
            return self._await_terminal(existing, payload, contracts, attempts, as_of)

        return ExecutionResult(
            status=ExecutionStatus.FAILED,
            client_order_id=client_order_id,
            contracts_ordered=contracts,
            request=payload,
            attempts=attempts,
            detail=f"submission failed and no order exists under this id: {error}",
            divergences=self._reconcile(),
            as_of=as_of,
        )

    def _finalise(
        self,
        order: BrokerOrder,
        payload: dict[str, Any],
        contracts: int,
        attempts: int,
        as_of: datetime,
    ) -> ExecutionResult:
        """Classify a terminal order, and reconcile whatever it left behind."""
        if order.is_partial:
            # F-13: one leg on and one leg off is naked short exposure. This is
            # the single worst state the system can be in, so it is CRITICAL
            # and goes straight to the Claims Desk.
            status = ExecutionStatus.PARTIAL
            detail = (
                f"partial fill: {order.filled_qty} of {order.ordered_qty} spreads. "
                "LEG_RISK — the unfilled leg leaves uncovered exposure."
            )
        elif order.state is OrderState.FILLED:
            status = ExecutionStatus.FILLED
            detail = f"filled {order.filled_qty} spreads at {order.filled_avg_price}"
        elif order.state is OrderState.REJECTED:
            status = ExecutionStatus.REJECTED
            detail = "the broker rejected the order"
        else:
            status = ExecutionStatus.CANCELLED
            detail = f"terminal as {order.state} with no fill"

        return ExecutionResult(
            status=status,
            client_order_id=order.client_order_id,
            broker_order_id=order.broker_order_id,
            contracts_ordered=contracts,
            contracts_filled=order.filled_qty,
            filled_avg_price=order.filled_avg_price,
            request=payload,
            response=order.raw,
            attempts=attempts,
            detail=detail,
            divergences=self._reconcile(),
            as_of=as_of,
        )

    # -- reconciliation --------------------------------------------------

    def _reconcile(self) -> tuple[str, ...]:
        """FR-089: after every attempt, ask the broker what it actually holds.

        Divergence is not an error to raise here — it is a finding to record.
        The caller turns it into a `risk_event`, and F-19 forces MANAGE_ONLY
        when a position exists that no policy explains.
        """
        try:
            self._bucket.acquire()
            positions: list[BrokerPosition] = self._broker.list_positions()
        except Exception as exc:
            return (f"position reconciliation failed: {type(exc).__name__}: {exc}",)

        return tuple(
            f"{position.symbol}: broker holds {position.qty}"
            for position in positions
            if position.qty != 0
        )

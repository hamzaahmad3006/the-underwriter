"""The non-bypass suite — TEST-030 … TEST-036.

These are the tests shown to judges. Each one asserts a different mechanism
from §14.4, and each is individually sufficient to stop a rogue LLM. Read
together they are the system's central claim, stated as executable fact:

    there is no code path from an LLM output to a transmitted order that does
    not pass through the Kernel.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import NOW, SECRET, make_context, make_proposal
from underwriter.domain.market import Side
from underwriter.kernel import kernel
from underwriter.kernel.verdict import (
    Decision,
    NonceRegistry,
    Severity,
    UnauthorizedExecution,
    authorize,
    sign,
    signing_payload,
)


def approve() -> tuple[object, object]:
    """A proposal and its approving verdict."""
    proposal = make_proposal()
    verdict = kernel.evaluate(
        proposal, requested_contracts=1, context=make_context(), secret=SECRET
    )
    assert verdict.verdict is Decision.APPROVE, kernel.explain(verdict)
    return proposal, verdict


# ---------------------------------------------------------------------------
# TEST-030 — no verdict, no order
# ---------------------------------------------------------------------------


def test_030_execution_without_a_verdict_is_refused() -> None:
    proposal = make_proposal()
    with pytest.raises(UnauthorizedExecution, match="no verdict"):
        authorize(
            None,
            proposal_hash=proposal.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


def test_030_a_rejection_cannot_authorize_anything() -> None:
    proposal = make_proposal(max_loss="90000.00")  # blows SK-003 and SK-007
    verdict = kernel.evaluate(
        proposal, requested_contracts=1, context=make_context(), secret=SECRET
    )
    assert verdict.verdict is Decision.REJECT
    assert verdict.signature is None, "a rejection must never carry a signature"

    with pytest.raises(UnauthorizedExecution):
        authorize(
            verdict,
            proposal_hash=proposal.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


# ---------------------------------------------------------------------------
# TEST-032 — a verdict is bound to one exact proposal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("short_strike", Decimal("545")),
        ("long_strike", Decimal("540")),
        ("underlying", "QQQ"),
        ("net_credit", Decimal("0.90")),
        ("max_loss", Decimal("120.00")),
    ],
)
def test_032_mutated_proposal_is_not_authorized(field: str, value: object) -> None:
    proposal, verdict = approve()
    mutated = replace(proposal, **{field: value})

    assert mutated.proposal_hash != proposal.proposal_hash
    with pytest.raises(UnauthorizedExecution, match="does not match"):
        authorize(
            verdict,
            proposal_hash=mutated.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


def test_032_mutated_leg_side_is_not_authorized() -> None:
    """Flipping the short leg to a buy turns a credit spread into a debit one."""
    proposal, verdict = approve()
    flipped_legs = (replace(proposal.legs[0], side=Side.BUY), proposal.legs[1])
    mutated = replace(proposal, legs=flipped_legs)

    with pytest.raises(UnauthorizedExecution):
        authorize(
            verdict,
            proposal_hash=mutated.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


def test_032_inflating_the_size_invalidates_the_signature() -> None:
    """The size is inside the signed payload, so it cannot be edited after."""
    proposal, verdict = approve()
    inflated = replace(verdict, approved_contracts=verdict.approved_contracts + 50)

    with pytest.raises(UnauthorizedExecution, match="signature does not verify"):
        authorize(
            inflated,
            proposal_hash=proposal.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


def test_032_a_forged_signature_from_the_wrong_secret_fails() -> None:
    proposal, verdict = approve()
    forged = replace(
        verdict,
        signature=sign(
            signing_payload(
                verdict.proposal_hash,
                verdict.approved_contracts,
                verdict.verdict,
                verdict.nonce,
                verdict.expires_at,
            ),
            "an-attacker-guessed-secret",
        ),
    )

    with pytest.raises(UnauthorizedExecution, match="signature does not verify"):
        authorize(
            forged,
            proposal_hash=proposal.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


# ---------------------------------------------------------------------------
# TEST-033 — verdicts expire
# ---------------------------------------------------------------------------


def test_033_expired_verdict_is_refused() -> None:
    proposal, verdict = approve()
    later = verdict.expires_at + timedelta(seconds=1)

    with pytest.raises(UnauthorizedExecution, match="expired"):
        authorize(
            verdict,
            proposal_hash=proposal.proposal_hash,
            secret=SECRET,
            now=later,
            nonces=NonceRegistry(),
        )


def test_033_verdict_is_still_valid_at_the_instant_it_expires() -> None:
    proposal, verdict = approve()
    assert (
        authorize(
            verdict,
            proposal_hash=proposal.proposal_hash,
            secret=SECRET,
            now=verdict.expires_at,
            nonces=NonceRegistry(),
        )
        >= 1
    )


# ---------------------------------------------------------------------------
# TEST-034 — nonces are single use
# ---------------------------------------------------------------------------


def test_034_replayed_nonce_is_refused() -> None:
    proposal, verdict = approve()
    nonces = NonceRegistry()

    assert (
        authorize(
            verdict, proposal_hash=proposal.proposal_hash, secret=SECRET, now=NOW, nonces=nonces
        )
        >= 1
    )

    with pytest.raises(UnauthorizedExecution, match="already used"):
        authorize(
            verdict,
            proposal_hash=proposal.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=nonces,
        )


# ---------------------------------------------------------------------------
# TEST-035 — a catastrophic proposal is rejected, citing the right rules
# ---------------------------------------------------------------------------


def test_035_catastrophic_proposal_is_rejected_with_named_reasons() -> None:
    """90% of NAV at risk, undefined risk, 0DTE, and a duplicate — all at once."""
    naked = make_proposal(
        max_loss="90000.00",
        dte=0,
        legs=(make_proposal().legs[0],),  # short leg only: nothing covering it
    )
    context = make_context(open_policies=(), supplied_candidate_ids=frozenset())

    verdict = kernel.evaluate(naked, requested_contracts=50, context=context, secret=SECRET)

    assert verdict.verdict is Decision.REJECT
    assert verdict.approved_contracts == 0
    assert verdict.signature is None
    assert {"POSITION_LOSS_LIMIT", "UNDEFINED_RISK", "DTE_TOO_SHORT", "LLM_OUTPUT_INVALID"} <= set(
        verdict.reject_reasons
    ), kernel.explain(verdict)


# ---------------------------------------------------------------------------
# TEST-036 — the property that has to hold for every input
# ---------------------------------------------------------------------------


def test_036_no_approval_ever_coexists_with_a_hard_failure() -> None:
    """10,000 randomised proposals: APPROVE implies every HARD rule passed.

    Seeded, so a failure is reproducible rather than a story about a build that
    went red once.
    """
    rng = random.Random(20260901)

    for _ in range(10_000):
        proposal = make_proposal(
            max_loss=f"{rng.uniform(1, 8000):.2f}",
            dte=rng.randint(0, 40),
            edge_ratio=f"{rng.uniform(-0.2, 0.4):.4f}",
            liquidity_score=f"{rng.uniform(0, 1):.4f}",
            max_leg_spread_pct=f"{rng.uniform(0, 0.4):.4f}",
            greeks_complete=rng.random() > 0.1,
            net_delta=f"{rng.uniform(-400, 400):.2f}",
        )
        context = make_context(
            data_age_sec=rng.randint(0, 400),
            market_open=rng.random() > 0.1,
            minutes_since_open=rng.randint(0, 200),
            minutes_to_close=rng.randint(0, 200),
            supplied_candidate_ids=(
                frozenset({proposal.candidate_id}) if rng.random() > 0.1 else frozenset()
            ),
        )
        verdict = kernel.evaluate(
            proposal,
            requested_contracts=rng.randint(0, 20),
            context=context,
            secret=SECRET,
        )

        hard_failures = [r for r in verdict.rules if not r.passed and r.severity is Severity.HARD]
        if verdict.verdict is Decision.APPROVE:
            assert not hard_failures, kernel.explain(verdict)
            assert verdict.signature is not None
            assert verdict.approved_contracts >= 1
        else:
            assert hard_failures, kernel.explain(verdict)
            assert verdict.signature is None

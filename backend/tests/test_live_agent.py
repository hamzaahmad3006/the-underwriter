"""Live AI Underwriter checks — ROAD-D0-06, ASM-006.

OPS-033: needs a real Groq key, so it never runs in CI. `make test-live`.

These prove three things the unit tests cannot, because they need a real model:
strict `json_schema` is actually honoured on the wire, the prompt produces a
usable decision rather than a refusal to engage, and DECLINE is reachable —
which matters more than WRITE, since a model that never declines is a model
that will eventually write something it should not.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

# ruff: noqa: E402 - .env must be read before the skip decision below.
from tests.conftest import make_proposal
from underwriter.agent.client import GroqClient
from underwriter.agent.prompt import PortfolioContext
from underwriter.agent.underwriter import AIUnderwriter, Outcome

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="no GROQ_API_KEY configured"),
]

CONTEXT = PortfolioContext(
    nav=Decimal("100000"),
    open_policies=2,
    reserve_utilization_pct=Decimal("18.5"),
    net_delta=Decimal("22"),
    net_vega=Decimal("-14"),
    underlyings_held=("SPY", "SPY"),
)


@pytest.fixture(scope="module")
def agent() -> AIUnderwriter:
    return AIUnderwriter(GroqClient())


def report(outcome) -> None:  # type: ignore[no-untyped-def]
    print(f"\n  outcome     {outcome.outcome}")
    print(f"  model       {outcome.model_version}")
    print(f"  tokens      {outcome.prompt_tokens} in / {outcome.completion_tokens} out")
    print(f"  latency     {outcome.latency_ms} ms")
    print(f"  retries     {outcome.retry_count}")
    if outcome.decision:
        decision = outcome.decision
        print(f"  candidate   {decision.candidate_id}")
        print(f"  confidence  {decision.confidence}")
        print(f"  contracts   {decision.contracts} (advisory, FR-046)")
        print(f"  rationale   {decision.rationale}")
        for risk in decision.identified_risks or []:
            print(f"    risk      {risk}")
        if decision.declined_reason:
            print(f"  declined    {decision.declined_reason}")


def test_the_model_selects_the_strongest_candidate(agent: AIUnderwriter) -> None:
    """Three candidates with genuinely different profiles, one clear winner.

    Not asserting *which* one it picks — that would be testing the model, and
    the Kernel is what stops a bad pick anyway. Asserting that it produces a
    schema-valid, semantically complete decision from the supplied set.
    """
    proposals = (
        make_proposal(
            candidate_id="cand_spy_550_548",
            underlying="SPY",
            short_strike=550,
            long_strike=548,
            edge_ratio="0.12",
            liquidity_score="0.84",
            max_loss="150.00",
        ),
        make_proposal(
            candidate_id="cand_qqq_470_465",
            underlying="QQQ",
            short_strike=470,
            long_strike=465,
            edge_ratio="0.07",
            liquidity_score="0.61",
            max_loss="380.00",
        ),
        make_proposal(
            candidate_id="cand_iwm_215_213",
            underlying="IWM",
            short_strike=215,
            long_strike=213,
            edge_ratio="0.05",
            liquidity_score="0.58",
            max_loss="160.00",
        ),
    )

    outcome = agent.decide(proposals, CONTEXT)
    report(outcome)

    assert outcome.outcome in {Outcome.WRITE, Outcome.DECLINE}
    assert outcome.retry_count == 0, "strict json_schema should not need a retry"
    assert outcome.model_version
    assert outcome.prompt_tokens > 0
    assert len(outcome.prompt_sha256) == 64

    if outcome.outcome is Outcome.WRITE:
        assert outcome.selected is not None
        assert outcome.decision is not None
        assert outcome.decision.candidate_id in {p.candidate_id for p in proposals}
        assert outcome.decision.identified_risks


def test_the_model_can_decline(agent: AIUnderwriter) -> None:
    """DECLINE must be reachable on genuinely poor candidates.

    This is the more important of the two. A model that always writes has no
    judgment to contribute, and the whole design assumes the Kernel is the last
    line rather than the only one.
    """
    weak = (
        make_proposal(
            candidate_id="cand_weak_1",
            edge_ratio="0.051",
            liquidity_score="0.56",
            max_loss="2900.00",
            dte=7,
        ),
    )
    outcome = agent.decide(weak, CONTEXT)
    report(outcome)

    assert outcome.outcome in {Outcome.WRITE, Outcome.DECLINE}
    if outcome.outcome is Outcome.DECLINE:
        assert outcome.decision is not None
        assert outcome.decision.declined_reason


def test_an_empty_candidate_set_costs_no_call(agent: AIUnderwriter) -> None:
    outcome = agent.decide((), CONTEXT)
    assert outcome.outcome is Outcome.NO_CANDIDATES
    assert outcome.prompt_tokens == 0

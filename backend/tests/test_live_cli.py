"""Live Alpaca CLI checks — ALP-005, ALP-007, §17.2.

OPS-033: needs the CLI binary and real credentials, so it never runs in CI.
`make test-live`, after `make tools`.

These prove the third surface is real rather than declared. The pre-flight is
only worth having if a second implementation genuinely accepts the payload the
first one built — and the only way to know that is to hand it over.
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
from underwriter.cli import doctor, is_available, validate_order
from underwriter.execution.order import build_entry_order, build_exit_order

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not is_available() or not os.environ.get("ALPACA_API_KEY"),
        reason="the Alpaca CLI or credentials are not available",
    ),
]


def test_alp005_doctor_reports_a_paper_profile_and_connectivity() -> None:
    """The readiness dependency: is this machine configured to trade at all?"""
    result = doctor()

    print(f"\n  doctor: ok={result.ok} paper={result.paper} — {result.detail}")
    assert result.paper is True, "the CLI is not on a paper profile"
    assert result.ok is True, result.detail


def test_alp007_the_cli_renders_the_same_entry_order_we_built() -> None:
    """A second implementation, given our payload, produces our payload."""
    payload = build_entry_order(make_proposal(), contracts=2)
    result = validate_order(payload)

    print(f"\n  pre-flight: ok={result.ok} — {result.detail}")
    for difference in result.differences:
        print(f"    {difference}")

    assert result.ok is True, result.detail
    assert result.skipped is False
    assert result.rendered is not None
    assert result.rendered["order_class"] == "mleg"


def test_alp007_the_cli_renders_the_same_exit_order_we_built() -> None:
    """The mirror order matters more: its intents are the ones easy to invert."""
    from underwriter.domain.proposal import Action

    payload = build_exit_order(
        make_proposal(action=Action.CLOSE), contracts=1, target_debit=Decimal("0.25")
    )
    result = validate_order(payload)

    assert result.ok is True, result.detail
    assert result.rendered is not None

    short_leg, long_leg = result.rendered["legs"]
    assert short_leg["position_intent"] == "buy_to_close"
    assert long_leg["position_intent"] == "sell_to_close"


def test_alp007_a_malformed_order_is_refused_before_it_reaches_the_broker() -> None:
    """The whole reason to run it: catch construction errors locally."""
    payload = build_entry_order(make_proposal(), contracts=1)
    payload["legs"][0]["position_intent"] = "not_a_real_intent"

    result = validate_order(payload)

    print(f"\n  malformed: ok={result.ok} — {result.detail[:120]}")
    assert result.ok is False
    assert result.blocks_execution is True


def test_alp007_the_preflight_transmits_nothing() -> None:
    """`--dry-run` prints the body and stops. Run twice; nothing accumulates."""
    payload = build_entry_order(make_proposal(), contracts=1)

    assert validate_order(payload).ok is True
    assert validate_order(payload).ok is True
    # A real submission would have collided on the deterministic
    # client_order_id the second time (FR-083). It did not, because nothing
    # was submitted.

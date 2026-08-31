"""Verdict mechanics — the parts of §14.2 and §14.4 the rule tests do not reach.

OPS-031 gates the Kernel at 100% line and branch coverage. That is not a
vanity number: an untested branch in this package is a branch that could
authorise an order, and nobody would know until it did.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.conftest import NOW, SECRET, make_context, make_proposal
from underwriter.kernel import kernel
from underwriter.kernel.verdict import (
    Decision,
    KernelVerdict,
    NonceRegistry,
    Severity,
    UnauthorizedExecution,
    authorize,
    mint,
    sign,
)


def approved_verdict() -> KernelVerdict:
    verdict = kernel.evaluate(
        make_proposal(), requested_contracts=1, context=make_context(), secret=SECRET
    )
    assert verdict.verdict is Decision.APPROVE
    return verdict


def test_approved_property_tracks_the_decision() -> None:
    assert approved_verdict().approved is True

    rejected = kernel.evaluate(
        make_proposal(dte=0), requested_contracts=1, context=make_context(), secret=SECRET
    )
    assert rejected.approved is False


def test_failed_rules_lists_only_the_failures() -> None:
    verdict = kernel.evaluate(
        make_proposal(dte=0, edge_ratio="-1"),
        requested_contracts=1,
        context=make_context(),
        secret=SECRET,
    )
    failed = verdict.failed_rules
    assert failed, "a rejected proposal must name its failures"
    assert all(not rule.passed for rule in failed)
    assert len(failed) < len(verdict.rules)


def test_signing_without_a_secret_is_refused() -> None:
    """SEC-005: an empty signing secret must fail loudly, not sign with ''."""
    with pytest.raises(ValueError, match="KERNEL_SIGNING_SECRET"):
        sign("payload", "")


def test_minting_an_approval_without_a_secret_raises() -> None:
    with pytest.raises(ValueError, match="KERNEL_SIGNING_SECRET"):
        mint(
            proposal_hash="a" * 64,
            verdict=Decision.APPROVE,
            approved_contracts=1,
            rules=(),
            reject_reasons=(),
            issued_at=NOW,
            ttl_sec=45,
            secret="",
        )


def test_a_rejection_needs_no_secret_because_it_is_never_signed() -> None:
    verdict = mint(
        proposal_hash="a" * 64,
        verdict=Decision.REJECT,
        approved_contracts=0,
        rules=(),
        reject_reasons=("SOMETHING",),
        issued_at=NOW,
        ttl_sec=45,
        secret="",
    )
    assert verdict.signature is None


def test_nonce_registry_reports_what_it_has_seen() -> None:
    registry = NonceRegistry()
    assert registry.seen("n1") is False
    assert registry.consume("n1") is True
    assert registry.seen("n1") is True
    assert registry.consume("n1") is False


def test_an_approval_stripped_of_its_signature_is_refused() -> None:
    """Deleting the signature must not read as "no signature required"."""
    verdict = replace(approved_verdict(), signature=None)
    with pytest.raises(UnauthorizedExecution, match="no signature"):
        authorize(
            verdict,
            proposal_hash=verdict.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


def test_an_approval_for_zero_contracts_is_refused() -> None:
    verdict = replace(approved_verdict(), approved_contracts=0)
    with pytest.raises(UnauthorizedExecution, match="approved_contracts"):
        authorize(
            verdict,
            proposal_hash=verdict.proposal_hash,
            secret=SECRET,
            now=NOW,
            nonces=NonceRegistry(),
        )


def test_is_expired_is_false_before_the_deadline() -> None:
    verdict = approved_verdict()
    assert verdict.is_expired(verdict.expires_at - timedelta(seconds=1)) is False
    assert verdict.is_expired(verdict.expires_at + timedelta(seconds=1)) is True


def test_rule_result_severity_helpers() -> None:
    verdict = kernel.evaluate(
        make_proposal(dte=0),
        requested_contracts=1,
        context=make_context(portfolio_net_delta="500"),
        secret=SECRET,
    )
    hard = [r for r in verdict.rules if r.is_hard_failure]
    soft = [r for r in verdict.rules if r.is_soft_failure]

    assert any(r.rule_id == "SK-011" for r in hard)
    assert any(r.rule_id == "SK-008" for r in soft)
    assert all(r.severity is Severity.HARD for r in hard)
    assert all(r.severity is Severity.SOFT for r in soft)


def test_an_unhashable_proposal_still_produces_a_rejection() -> None:
    """The last line of FR-062: even the ledger key failing cannot approve."""

    class Unhashable:
        action = make_proposal().action

        @property
        def proposal_hash(self) -> str:
            raise RuntimeError("hashing is broken")

    verdict = kernel.evaluate(
        Unhashable(),  # type: ignore[arg-type]
        requested_contracts=1,
        context=make_context(),
        secret=SECRET,
    )
    assert verdict.verdict is Decision.REJECT
    assert verdict.proposal_hash == "unhashable"
    assert verdict.signature is None

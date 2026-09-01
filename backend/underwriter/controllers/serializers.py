"""Turning domain objects into JSON the dashboard can render.

UI-002 drives the shape: every payload carries `as_of`, because no number may
appear in the UI without provenance. Decimals serialise as strings so the
browser's float arithmetic can never round a reserve.
"""

from __future__ import annotations

from typing import Any

from underwriter.kernel.verdict import KernelVerdict, RuleResult


def rule_to_dict(rule: RuleResult) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "name": rule.name,
        "passed": rule.passed,
        "severity": rule.severity,
        "observed": rule.observed,
        "limit": rule.limit,
        "message": rule.message,
        "reason_code": rule.reason_code or None,
    }


def verdict_to_dict(verdict: KernelVerdict) -> dict[str, Any]:
    """The full verdict, including every rule that passed (FR-061).

    The signature is deliberately **not** serialised. It authorises execution;
    it is not display data, and an endpoint that returns it is an endpoint that
    can leak one.
    """
    return {
        "verdict_id": verdict.verdict_id,
        "proposal_hash": verdict.proposal_hash,
        "verdict": verdict.verdict,
        "approved_contracts": verdict.approved_contracts,
        "reject_reasons": list(verdict.reject_reasons),
        "issued_at": verdict.issued_at.isoformat(),
        "expires_at": verdict.expires_at.isoformat(),
        "signed": verdict.signature is not None,
        "rules": [rule_to_dict(rule) for rule in verdict.rules],
        "rules_failed": len(verdict.failed_rules),
        "rules_evaluated": len(verdict.rules),
    }

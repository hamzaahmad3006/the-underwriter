"""Turning domain objects into JSON the dashboard can render.

UI-002 drives the shape: every payload carries `as_of`, because no number may
appear in the UI without provenance. Decimals serialise as strings so the
browser's float arithmetic can never round a reserve.
"""

from __future__ import annotations

from typing import Any

from underwriter.actuary.validation import Discard
from underwriter.domain.proposal import UnderwritingProposal
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


def proposal_to_dict(proposal: UnderwritingProposal) -> dict[str, Any]:
    """One Actuary-priced candidate, as the LLM and the dashboard both see it.

    Every figure is already computed and authoritative (§13.4): the model is
    told explicitly that it performs no arithmetic, and this payload is why it
    never needs to.
    """
    return {
        "candidate_id": proposal.candidate_id,
        "underlying": proposal.underlying,
        "structure": proposal.structure,
        "expiry": proposal.expiry.isoformat(),
        "dte": proposal.dte,
        "short_strike": str(proposal.short_strike),
        "long_strike": str(proposal.long_strike),
        "width": str(proposal.width),
        "net_credit": str(proposal.net_credit),
        "max_profit": str(proposal.max_profit),
        "max_loss": str(proposal.max_loss),
        "capital_reserve": str(proposal.capital_reserve),
        "breakeven": str(proposal.breakeven),
        "credit_to_width": str(proposal.credit_to_width),
        # NG-02: delta-implied and approximate. The UI must say so.
        "p_loss_proxy": str(proposal.p_loss_proxy),
        "expected_value": str(proposal.expected_value),
        "edge_ratio": str(proposal.edge_ratio),
        "liquidity_score": str(proposal.liquidity_score),
        "max_leg_spread_pct": str(proposal.max_leg_spread_pct),
        "short_delta": str(proposal.short_delta),
        "net_delta": str(proposal.net_delta),
        "net_vega": str(proposal.net_vega),
        "snapshot_hash": proposal.snapshot_hash,
        "proposal_hash": proposal.proposal_hash,
        "legs": [
            {
                "symbol": leg.symbol,
                "side": leg.side,
                "right": leg.right,
                "strike": str(leg.strike),
                "expiry": leg.expiry.isoformat(),
                "ratio_qty": leg.ratio_qty,
            }
            for leg in proposal.legs
        ],
    }


def discard_to_dict(discard: Discard) -> dict[str, Any]:
    """A candidate that never reached the model, and why (FR-023, UI-006)."""
    return {
        "candidate_id": discard.candidate_id,
        "reason": discard.reason,
        "detail": discard.detail,
    }

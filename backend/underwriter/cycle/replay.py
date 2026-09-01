"""Deterministic replay — API-033, NFR-007, AC-08.

Take a stored decision, re-run the Actuary and the Kernel over the exact inputs
that produced it, and diff the result against what was recorded. The diff must
be empty.

This is the auditability proof, and it is only possible because of choices made
much earlier: the Actuary reads its clock from the snapshot rather than the
wall, all arithmetic is `Decimal`, and the snapshot hashes stably. Any one of
those missing and this endpoint would report drift on every call.

A mismatch is not a 500. It is `REPLAY_MISMATCH`, a CRITICAL risk event, and a
reason to stop trading — determinism is the property the whole audit story
rests on, and losing it quietly would be worse than any single bad trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from underwriter.actuary.engine import price_put_credit_spreads
from underwriter.db.models import Candidate, KernelDecision, MarketSnapshotRow
from underwriter.domain.market import ContractQuote, MarketSnapshot, OptionRight
from underwriter.domain.money import to_decimal


@dataclass(frozen=True, slots=True)
class FieldDiff:
    field: str
    recorded: str | None
    replayed: str | None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """API-033's response. `deterministic` is the assertion under test."""

    decision_id: str
    deterministic: bool
    snapshot_hash: str | None = None
    replayed_hash: str | None = None
    diff: tuple[FieldDiff, ...] = field(default_factory=tuple)
    detail: str = ""
    as_of: datetime = datetime(1970, 1, 1, tzinfo=UTC)


class ReplayUnavailableError(RuntimeError):
    """The stored inputs are not sufficient to replay this decision."""


def rebuild_snapshot(row: MarketSnapshotRow) -> MarketSnapshot:
    """Reconstruct the exact `MarketSnapshot` the Actuary saw.

    Rebuilt from the stored chain rather than refetched. Refetching would test
    whether the market moved, which it always has; this tests whether the same
    inputs still produce the same outputs.
    """
    payload: Any = row.chain_json or {}
    quotes: list[ContractQuote] = []

    for quote in payload.get("quotes", []):
        quotes.append(
            ContractQuote(
                symbol=quote["symbol"],
                underlying=quote["underlying"],
                right=OptionRight.PUT,
                strike=to_decimal(quote["strike"], field="strike"),
                expiry=date.fromisoformat(quote["expiry"]),
                bid=to_decimal(quote["bid"], field="bid"),
                ask=to_decimal(quote["ask"], field="ask"),
                bid_size=int(quote["bid_size"]),
                ask_size=int(quote["ask_size"]),
                fetched_at=datetime.fromisoformat(quote["fetched_at"]),
                open_interest=quote.get("open_interest"),
                implied_volatility=(
                    to_decimal(quote["iv"], field="iv") if quote.get("iv") else None
                ),
                delta=to_decimal(quote["delta"], field="delta") if quote.get("delta") else None,
                vega=to_decimal(quote["vega"], field="vega") if quote.get("vega") else None,
                source=row.source,
            )
        )

    return MarketSnapshot(
        as_of=row.fetched_at,
        underlying_prices=(
            {row.underlying: row.underlying_price} if row.underlying_price is not None else {}
        ),
        quotes=tuple(quotes),
    )


def _compare(name: str, recorded: Decimal | None, replayed: Decimal | None) -> FieldDiff | None:
    if recorded == replayed:
        return None
    return FieldDiff(
        field=name,
        recorded=None if recorded is None else str(recorded),
        replayed=None if replayed is None else str(replayed),
    )


def replay_decision(session: Session, decision_id: str) -> ReplayResult:
    """Re-run the Actuary over stored inputs and diff against the record."""
    now = datetime.now(UTC)

    verdict = session.get(KernelDecision, decision_id)
    if verdict is None:
        raise ReplayUnavailableError(f"no kernel decision with id {decision_id}")

    candidate = session.get(Candidate, verdict.candidate_id) if verdict.candidate_id else None
    if candidate is None:
        raise ReplayUnavailableError("the verdict references no stored candidate")

    snapshot_row = (
        session.get(MarketSnapshotRow, candidate.snapshot_id) if candidate.snapshot_id else None
    )
    if snapshot_row is None:
        raise ReplayUnavailableError("the candidate references no stored snapshot")

    snapshot = rebuild_snapshot(snapshot_row)
    replayed_hash = snapshot.snapshot_hash

    if replayed_hash != snapshot_row.snapshot_hash:
        # The stored chain no longer hashes to what was recorded, so nothing
        # below would mean anything.
        return ReplayResult(
            decision_id=decision_id,
            deterministic=False,
            snapshot_hash=snapshot_row.snapshot_hash,
            replayed_hash=replayed_hash,
            diff=(FieldDiff("snapshot_hash", snapshot_row.snapshot_hash, replayed_hash),),
            detail="the stored snapshot no longer hashes to its recorded value",
            as_of=now,
        )

    priced = price_put_credit_spreads(snapshot)
    match = next((p for p in priced.proposals if p.proposal_hash == candidate.proposal_hash), None)

    if match is None:
        return ReplayResult(
            decision_id=decision_id,
            deterministic=False,
            snapshot_hash=snapshot_row.snapshot_hash,
            replayed_hash=replayed_hash,
            diff=(FieldDiff("proposal_hash", candidate.proposal_hash, None),),
            detail="the recorded candidate was not reproduced from the same snapshot",
            as_of=now,
        )

    diffs = [
        d
        for d in (
            _compare("net_credit", candidate.net_credit, match.net_credit),
            _compare("max_profit", candidate.max_profit, match.max_profit),
            _compare("max_loss", candidate.max_loss, match.max_loss),
            _compare("capital_reserve", candidate.capital_reserve, match.capital_reserve),
            _compare("breakeven", candidate.breakeven, match.breakeven),
            _compare("expected_value", candidate.expected_value, match.expected_value),
            _compare("edge_ratio", candidate.edge_ratio, match.edge_ratio),
            _compare("liquidity_score", candidate.liquidity_score, match.liquidity_score),
            _compare("short_delta", candidate.short_delta, match.short_delta),
        )
        if d is not None
    ]

    return ReplayResult(
        decision_id=decision_id,
        deterministic=not diffs,
        snapshot_hash=snapshot_row.snapshot_hash,
        replayed_hash=replayed_hash,
        diff=tuple(diffs),
        detail=(
            "every value reproduced exactly from the stored inputs"
            if not diffs
            else f"{len(diffs)} field(s) differ — determinism is broken (NFR-007)"
        ),
        as_of=now,
    )

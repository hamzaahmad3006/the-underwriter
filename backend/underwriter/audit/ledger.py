"""Append-only ledger with a SHA-256 hash chain (DB-017, API-061).

Each record hashes its own payload together with the previous record's hash, so
altering any historical row invalidates every hash after it. That is what turns
"we log everything" into something checkable: `verify_chain` walks the whole
ledger and names the first sequence number where the chain breaks.

The hash deliberately covers the *content* of a record, not its `seq`. Sequence
numbers come from the database; content is what someone would want to change.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from underwriter.db.base import utc_now
from underwriter.db.models import AuditLog
from underwriter.domain.hashing import canonical_json

GENESIS_HASH = "0" * 64


class Actor(StrEnum):
    """Who did it. The operator is on this list, not above it (SEC-012)."""

    SCHEDULER = "SCHEDULER"
    ACTUARY = "ACTUARY"
    UNDERWRITER = "UNDERWRITER"
    KERNEL = "KERNEL"
    EXECUTION = "EXECUTION"
    CLAIMS = "CLAIMS"
    OPERATOR = "OPERATOR"


def record_payload(
    *,
    occurred_at: datetime,
    correlation_id: str | None,
    actor: str,
    action: str,
    entity_type: str | None,
    entity_id: str | None,
    before: Any,
    after: Any,
    prev_hash: str,
) -> str:
    """The exact bytes a record's hash covers."""
    return canonical_json(
        {
            "occurred_at": occurred_at,
            "correlation_id": correlation_id,
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before": before,
            "after": after,
            "prev_hash": prev_hash,
        }
    )


def compute_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def latest_hash(session: Session) -> str:
    """The tip of the chain, or the genesis value on an empty ledger."""
    row = session.execute(
        select(AuditLog.record_hash).order_by(AuditLog.seq.desc()).limit(1)
    ).scalar_one_or_none()
    return row or GENESIS_HASH


def append(
    session: Session,
    *,
    actor: Actor | str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    before: Any = None,
    after: Any = None,
    correlation_id: str | None = None,
    occurred_at: datetime | None = None,
) -> AuditLog:
    """Add one record, linked to the current tip.

    The caller owns the transaction. That is deliberate: an audit record and
    the change it describes must commit together or not at all, otherwise the
    ledger can claim something the book never did.
    """
    when = occurred_at or utc_now()
    prev = latest_hash(session)

    payload = record_payload(
        occurred_at=when,
        correlation_id=correlation_id,
        actor=str(actor),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        prev_hash=prev,
    )

    record = AuditLog(
        occurred_at=when,
        correlation_id=correlation_id,
        actor=str(actor),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=before,
        after_json=after,
        prev_hash=prev,
        record_hash=compute_hash(payload),
    )
    session.add(record)
    session.flush()  # assign seq now, so callers can reference it
    return record


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """API-061's response shape."""

    valid: bool
    records_checked: int
    first_break_seq: int | None
    detail: str


def verify_chain(session: Session) -> ChainVerification:
    """Walk the whole ledger and report the first break, if any.

    Two failures are distinguished because they mean different things: a
    `record_hash` that does not match its own content means the row was
    edited, while a `prev_hash` that does not match the previous row's hash
    means a row was inserted, removed or reordered.
    """
    rows = session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars().all()

    expected_prev = GENESIS_HASH
    for index, row in enumerate(rows, start=1):
        if row.prev_hash != expected_prev:
            return ChainVerification(
                valid=False,
                records_checked=index,
                first_break_seq=row.seq,
                detail=(
                    f"seq {row.seq} links to {row.prev_hash!r}, but the previous "
                    f"record hashes to {expected_prev!r} — a record was inserted, "
                    "removed or reordered"
                ),
            )

        payload = record_payload(
            occurred_at=row.occurred_at,
            correlation_id=row.correlation_id,
            actor=row.actor,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            before=row.before_json,
            after=row.after_json,
            prev_hash=row.prev_hash or GENESIS_HASH,
        )
        if compute_hash(payload) != row.record_hash:
            return ChainVerification(
                valid=False,
                records_checked=index,
                first_break_seq=row.seq,
                detail=f"seq {row.seq} does not hash to its stored value — the row was edited",
            )

        expected_prev = row.record_hash

    return ChainVerification(
        valid=True,
        records_checked=len(rows),
        first_break_seq=None,
        detail=f"{len(rows)} records verified from genesis",
    )

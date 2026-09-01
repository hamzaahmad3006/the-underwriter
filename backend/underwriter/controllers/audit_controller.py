"""Audit endpoints — API-060, API-061, API-062.

API-062 exists so a judge can take the whole ledger away and check it
independently. API-061 is the same claim in one request: walk the chain and
name the first sequence number where it breaks.

All three are unauthenticated. The audit trail is the evidence, and evidence
behind a login is evidence nobody checks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from underwriter.audit.ledger import verify_chain
from underwriter.db import session_scope
from underwriter.db.models import AuditLog


def _row_to_dict(row: AuditLog) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "occurred_at": row.occurred_at.isoformat(),
        "correlation_id": row.correlation_id,
        "actor": row.actor,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "before": row.before_json,
        "after": row.after_json,
        "prev_hash": row.prev_hash,
        "record_hash": row.record_hash,
    }


def log(correlation_id: str | None, actor: str | None, limit: int) -> dict[str, Any]:
    """API-060 — the audit trail, newest first."""
    with session_scope() as session:
        query = select(AuditLog).order_by(AuditLog.seq.desc()).limit(limit)
        if correlation_id:
            query = query.where(AuditLog.correlation_id == correlation_id)
        if actor:
            query = query.where(AuditLog.actor == actor)

        rows = session.execute(query).scalars().all()
        total = session.execute(select(AuditLog.seq).order_by(AuditLog.seq.desc()).limit(1))
        highest = total.scalar_one_or_none() or 0

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "records": [_row_to_dict(row) for row in rows],
            "returned": len(rows),
            "highest_seq": highest,
        }


def verify() -> dict[str, Any]:
    """API-061 — walk the hash chain from genesis.

    AC-08. `valid` false is not a server error: the endpoint's job is to report
    the break, and a 500 would hide exactly the thing it exists to surface.
    """
    with session_scope() as session:
        result = verify_chain(session)
        return {
            "as_of": datetime.now(UTC).isoformat(),
            "valid": result.valid,
            "records_checked": result.records_checked,
            "first_break_seq": result.first_break_seq,
            "detail": result.detail,
        }


def export(format_: str) -> dict[str, Any]:
    """API-062 — the full ledger, for a judge to verify elsewhere."""
    with session_scope() as session:
        rows = session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars().all()
        verification = verify_chain(session)

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "format": format_,
            "records": [_row_to_dict(row) for row in rows],
            "count": len(rows),
            # Exported alongside the data so the file is self-checking.
            "chain": {
                "valid": verification.valid,
                "records_checked": verification.records_checked,
                "first_break_seq": verification.first_break_seq,
            },
        }

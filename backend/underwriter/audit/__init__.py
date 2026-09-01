"""The Audit Ledger — `underwriter/audit/` (§11.7).

Append-only, one correlation id per cycle, and a SHA-256 chain linking each
record to the one before it. The chain is the point: it makes tampering
detectable rather than merely discouraged, and `API-061` can prove integrity to
a judge in one request.
"""

from underwriter.audit.ledger import Actor, ChainVerification, append, verify_chain

__all__ = ["Actor", "ChainVerification", "append", "verify_chain"]

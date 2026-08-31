"""The verdict — and the signature that makes it non-bypassable.

FR-063: on APPROVE the Kernel mints an HMAC-SHA256 over the canonical
serialisation of (proposal_hash, contracts, verdict, nonce, expires_at).

TD-03 is the reason this is a signature and not a convention: call ordering, a
decorator, or an interface contract can all be refactored away by a tired
developer at 2am. A signature cannot, and it makes the system's central claim
mechanically testable (TEST-030 … TEST-034).

Determinism note (SK-P1): the *decision* is deterministic. `verdict_id` and
`nonce` are deliberately random — a predictable nonce would be forgeable, which
is the opposite of the property this module exists to provide.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256

from underwriter.domain.hashing import canonical_json


class UnauthorizedExecution(RuntimeError):
    """Raised when execution is attempted without a valid, current verdict.

    Every path that reaches the Alpaca transport raises this rather than
    returning a falsy value, so a caller cannot ignore it by accident.
    """


class Severity(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


# Reason codes carried on HARD failures (§14.3, rightmost column).
FAIL_CLOSED = "KERNEL_FAIL_CLOSED"


@dataclass(frozen=True, slots=True)
class RuleResult:
    """One rule's finding. Recorded whether it passed or failed (FR-061)."""

    rule_id: str
    name: str
    passed: bool
    severity: Severity
    observed: str
    limit: str
    message: str
    reason_code: str = ""

    @property
    def is_hard_failure(self) -> bool:
        return not self.passed and self.severity is Severity.HARD

    @property
    def is_soft_failure(self) -> bool:
        return not self.passed and self.severity is Severity.SOFT


@dataclass(frozen=True, slots=True)
class KernelVerdict:
    """The only artifact that authorises an order.

    `signature` is present if and only if the verdict approves. A REJECT is
    unsigned by construction, so there is nothing to replay.
    """

    verdict_id: str
    proposal_hash: str
    verdict: Decision
    approved_contracts: int
    rules: tuple[RuleResult, ...]
    reject_reasons: tuple[str, ...]
    nonce: str
    issued_at: datetime
    expires_at: datetime
    signature: str | None = None

    @property
    def approved(self) -> bool:
        return self.verdict is Decision.APPROVE

    @property
    def failed_rules(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.rules if not r.passed)

    def is_expired(self, now: datetime) -> bool:
        return now > self.expires_at


def signing_payload(
    proposal_hash: str,
    approved_contracts: int,
    verdict: Decision,
    nonce: str,
    expires_at: datetime,
) -> str:
    """Canonical bytes the signature covers (FR-063).

    `proposal_hash` is in here, which is what binds a verdict to one exact
    proposal: mutate the strike, the size, the symbol or the side and the hash
    changes, so the signature no longer verifies (TEST-032).
    """
    return canonical_json(
        {
            "proposal_hash": proposal_hash,
            "approved_contracts": approved_contracts,
            "verdict": verdict,
            "nonce": nonce,
            "expires_at": expires_at,
        }
    )


def sign(payload: str, secret: str) -> str:
    """HMAC-SHA256, hex. The secret never leaves the Kernel process."""
    if not secret:
        raise ValueError("KERNEL_SIGNING_SECRET is empty; refusing to sign (SEC-005)")
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()


def new_nonce() -> str:
    return secrets.token_hex(16)


def new_verdict_id() -> str:
    return f"vd_{secrets.token_hex(8)}"


def mint(
    *,
    proposal_hash: str,
    verdict: Decision,
    approved_contracts: int,
    rules: tuple[RuleResult, ...],
    reject_reasons: tuple[str, ...],
    issued_at: datetime,
    ttl_sec: int,
    secret: str,
) -> KernelVerdict:
    """Build a verdict, signing it only if it approves."""
    nonce = new_nonce()
    expires_at = issued_at + timedelta(seconds=ttl_sec)
    signature: str | None = None

    if verdict is Decision.APPROVE:
        signature = sign(
            signing_payload(proposal_hash, approved_contracts, verdict, nonce, expires_at),
            secret,
        )

    return KernelVerdict(
        verdict_id=new_verdict_id(),
        proposal_hash=proposal_hash,
        verdict=verdict,
        approved_contracts=approved_contracts,
        rules=rules,
        reject_reasons=reject_reasons,
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        signature=signature,
    )


@dataclass(slots=True)
class NonceRegistry:
    """Single-use nonces (§14.4, mechanism 5).

    In-memory here; the durable implementation is a unique constraint in
    `kernel_decisions`. Both must reject the second use of a nonce, which is
    what stops a captured verdict being replayed inside its TTL (TEST-034).
    """

    _consumed: set[str] = field(default_factory=set)

    def consume(self, nonce: str) -> bool:
        """True on first use, False every time after."""
        if nonce in self._consumed:
            return False
        self._consumed.add(nonce)
        return True

    def seen(self, nonce: str) -> bool:
        return nonce in self._consumed


def authorize(
    verdict: KernelVerdict | None,
    *,
    proposal_hash: str,
    secret: str,
    now: datetime,
    nonces: NonceRegistry,
) -> int:
    """Gate every order. Returns the authorised contract count or raises.

    This is the single choke point named in FR-080. All five independent
    mechanisms of §14.4 are enforced here except credential isolation, which is
    structural rather than checkable at runtime.
    """
    if verdict is None:
        raise UnauthorizedExecution("no verdict supplied (TEST-030)")

    if verdict.verdict is not Decision.APPROVE:
        raise UnauthorizedExecution(f"verdict is {verdict.verdict}, not APPROVE")

    if verdict.signature is None:
        raise UnauthorizedExecution("approved verdict carries no signature")

    if verdict.approved_contracts < 1:
        raise UnauthorizedExecution("approved_contracts < 1")

    if verdict.is_expired(now):
        raise UnauthorizedExecution(
            f"verdict expired at {verdict.expires_at.isoformat()} (TEST-033)"
        )

    if verdict.proposal_hash != proposal_hash:
        raise UnauthorizedExecution("verdict does not match this proposal (TEST-032)")

    expected = sign(
        signing_payload(
            verdict.proposal_hash,
            verdict.approved_contracts,
            verdict.verdict,
            verdict.nonce,
            verdict.expires_at,
        ),
        secret,
    )
    if not hmac.compare_digest(expected, verdict.signature):
        raise UnauthorizedExecution("signature does not verify")

    if not nonces.consume(verdict.nonce):
        raise UnauthorizedExecution(f"nonce {verdict.nonce} already used (TEST-034)")

    return verdict.approved_contracts

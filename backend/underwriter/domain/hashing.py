"""Canonical serialisation.

Two things depend on this being byte-stable: FR-027 (the same snapshot must
produce the same proposal set forever) and FR-063 (the HMAC signature covers a
canonical serialisation, so a re-serialised proposal must hash identically).

Decimals serialise via `str`, not `float`, so `1.10` never becomes `1.1000000001`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

type Canonical = str | int | bool | list["Canonical"] | dict[str, "Canonical"] | None


def canonicalize(value: object) -> Canonical:
    """Reduce a value to JSON primitives with no float anywhere."""
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        # Deliberate: a float in a hashed payload is a determinism bug (NFR-013).
        raise TypeError("float is not canonicalizable; use Decimal (NFR-013)")
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(k): canonicalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, Sequence):
        return [canonicalize(v) for v in value]
    raise TypeError(f"{type(value).__name__} is not canonicalizable")


def canonical_json(value: object) -> str:
    """Deterministic JSON: sorted keys, no whitespace, no floats."""
    return json.dumps(canonicalize(value), sort_keys=True, separators=(",", ":"))


def sha256_of(value: object) -> str:
    """Hex SHA-256 of the canonical form."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

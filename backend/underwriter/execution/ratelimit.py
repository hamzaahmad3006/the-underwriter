"""Client-side token bucket — FR-088, ALP-023.

Alpaca documents 200 requests per minute on the trading API. The bucket is set
below that deliberately: a client that paces itself never discovers what the
server does when it stops being patient, and a 429 during a cancel is a 429 at
the worst possible moment.

Not thread-safe by design — OPS-020 runs exactly one instance with one
scheduler, so contention here would mean something else has already gone wrong.
"""

from __future__ import annotations

import time
from typing import Any

TRADING_RPM = 120  # against a documented ceiling of 200 (ALP-023)


class TokenBucket:
    """Refills continuously; blocks when empty."""

    def __init__(
        self,
        *,
        rate_per_minute: int = TRADING_RPM,
        burst: int | None = None,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
    ) -> None:
        if rate_per_minute < 1:
            raise ValueError("rate_per_minute must be at least 1")

        self._rate_per_sec = rate_per_minute / 60.0
        self._capacity = float(burst if burst is not None else max(1, rate_per_minute // 6))
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_sec)

    def acquire(self, tokens: float = 1.0) -> float:
        """Take a token, waiting if necessary. Returns seconds waited."""
        if tokens > self._capacity:
            raise ValueError(f"cannot acquire {tokens} tokens from a bucket of {self._capacity}")

        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return 0.0

        deficit = tokens - self._tokens
        wait = deficit / self._rate_per_sec
        self._sleep(wait)
        self._refill()
        self._tokens = max(0.0, self._tokens - tokens)
        return wait

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens

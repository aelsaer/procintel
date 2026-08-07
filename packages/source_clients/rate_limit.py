"""Async token-bucket rate limiter.

Sized in requests/minute rather than requests/second because that's how the
source contracts express their limits (e.g. KHMDHS's 180-240 req/min target
band, description.txt §16.3).
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class RateLimiter(Protocol):
    async def acquire(self, cost: float = 1.0) -> None: ...


class TokenBucket:
    def __init__(self, rate_per_minute: float, burst: float | None = None) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self._rate_per_second = rate_per_minute / 60.0
        # ~10 seconds worth of tokens as the default burst allowance.
        self._capacity = burst if burst is not None else rate_per_minute / 6
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        if cost > self._capacity:
            raise ValueError("cost exceeds bucket capacity")
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_second)
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                wait_seconds = (cost - self._tokens) / self._rate_per_second
                await asyncio.sleep(wait_seconds)

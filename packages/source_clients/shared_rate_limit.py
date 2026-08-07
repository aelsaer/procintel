"""Cross-process sliding-window limiter backed by a shared local volume."""

from __future__ import annotations

import asyncio
import fcntl
import json
import time
from pathlib import Path
from typing import Callable

from .rate_limit import RateLimiter, TokenBucket


class SharedSlidingWindowLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        state_path: str,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._request_limit = requests_per_minute
        self._state_path = Path(state_path)
        self._window_seconds = window_seconds
        self._clock = clock

    def _reserve_or_wait(self) -> float:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                stored = json.load(handle)
            except (json.JSONDecodeError, TypeError):
                stored = []
            if not isinstance(stored, list):
                stored = []
            now = self._clock()
            cutoff = now - self._window_seconds
            timestamps = sorted(
                float(value)
                for value in stored
                if isinstance(value, (int, float)) and float(value) > cutoff
            )
            if len(timestamps) < self._request_limit:
                timestamps.append(now)
                handle.seek(0)
                handle.truncate()
                json.dump(timestamps, handle, separators=(",", ":"))
                handle.flush()
                return 0.0
            return max(0.01, timestamps[0] + self._window_seconds - now)

    async def acquire(self, cost: float = 1.0) -> None:
        if cost != 1.0:
            raise ValueError("shared sliding-window limiter supports unit-cost requests only")
        while True:
            wait_seconds = await asyncio.to_thread(self._reserve_or_wait)
            if wait_seconds <= 0:
                return
            await asyncio.sleep(wait_seconds)


def configured_rate_limiter(
    rate_per_minute: float,
    state_path: str | None,
    *,
    burst: float | None = None,
) -> RateLimiter:
    if state_path:
        return SharedSlidingWindowLimiter(int(rate_per_minute), state_path)
    return TokenBucket(rate_per_minute, burst=burst)

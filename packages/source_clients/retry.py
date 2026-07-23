"""Retry/backoff/circuit-breaker helpers shared by every connector.

Implements description.txt §36: 429 -> read Retry-After, pause, no aggressive
retries; 5xx -> exponential backoff + jitter + max attempts + circuit
breaker.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import httpx
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt


class RateLimitedError(Exception):
    """HTTP 429. Carries the Retry-After hint in seconds, if the server sent one."""

    def __init__(self, retry_after: float | None) -> None:
        self.retry_after = retry_after
        super().__init__(f"rate limited, retry_after={retry_after}")


class TransientServerError(Exception):
    """HTTP 5xx — worth retrying with backoff."""


class CircuitOpenError(Exception):
    """Too many consecutive failures; short-circuit further attempts."""


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    _consecutive_failures: int = field(default=0, init=False)
    _open: bool = field(default=False, init=False)

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._open = False

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._open = True

    def raise_if_open(self) -> None:
        if self._open:
            raise CircuitOpenError(
                f"circuit open after {self._consecutive_failures} consecutive failures"
            )


def _wait_for_retry(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RateLimitedError):
        if exc.retry_after is not None:
            return exc.retry_after
        # No Retry-After header — observed against the real ΚΗΜΔΗΣ/Διαύγεια
        # APIs to be the common case, not the exception. A generic-transient
        # backoff (capped at 60s, starting from ~1s) rarely spans a real
        # per-minute rate-limit window before `max_attempts` is exhausted;
        # back off harder specifically for an unexplained 429 so repeated
        # attempts have a real chance of landing after the window resets.
        base = min(90.0, 10.0 * (2 ** (retry_state.attempt_number - 1)))
        return random.uniform(base / 2, base)
    base = min(60.0, 1.0 * (2 ** (retry_state.attempt_number - 1)))
    return random.uniform(0, base)  # full jitter


def retrying(max_attempts: int = 5):
    """Decorator: exponential backoff + jitter on 5xx/connection errors,
    Retry-After-aware wait on 429, bounded attempts."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=_wait_for_retry,
        retry=retry_if_exception_type(
            (RateLimitedError, TransientServerError, httpx.TransportError)
        ),
    )


def raise_for_retryable_status(response: httpx.Response) -> None:
    """Translate a response's status code into the retry exceptions above.
    No-op for 2xx and non-429 4xx responses."""
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise RateLimitedError(float(retry_after) if retry_after else None)
    if response.status_code >= 500:
        raise TransientServerError(f"HTTP {response.status_code}")

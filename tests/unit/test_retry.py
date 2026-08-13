import httpx
import pytest

from packages.source_clients.retry import (
    CircuitBreaker,
    CircuitOpenError,
    RateLimitedError,
    TransientServerError,
    _wait_for_retry,
    raise_for_retryable_status,
)


def _response(status_code: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, headers=headers or {}, request=httpx.Request("GET", "https://example.test"))


class _FakeOutcome:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def exception(self) -> Exception:
        return self._exc


class _FakeRetryState:
    """Tenacity's real `RetryCallState` needs a live retry context to
    construct; `_wait_for_retry` only ever reads `.outcome.exception()` and
    `.attempt_number`, so a minimal stand-in is enough to test it directly
    without actually driving a retried call end to end."""

    def __init__(self, exc: Exception, attempt_number: int) -> None:
        self.outcome = _FakeOutcome(exc)
        self.attempt_number = attempt_number


def test_rate_limited_with_retry_after_uses_the_exact_hint():
    state = _FakeRetryState(RateLimitedError(12.0), attempt_number=3)
    assert _wait_for_retry(state) == 12.0


def test_rate_limited_without_retry_after_backs_off_harder_than_generic_transient():
    # a bare 429 with no Retry-After header — observed to be the common
    # case against the real ΚΗΜΔΗΣ/Διαύγεια APIs, per packages/source_clients/
    # retry.py's own comment on why this needs a longer floor than the
    # generic transient-error backoff below
    wait = _wait_for_retry(_FakeRetryState(RateLimitedError(None), attempt_number=1))
    assert 5.0 <= wait <= 10.0


def test_rate_limited_without_retry_after_backoff_is_capped_at_90_seconds():
    wait = _wait_for_retry(_FakeRetryState(RateLimitedError(None), attempt_number=10))
    assert 45.0 <= wait <= 90.0


def test_generic_transient_error_backoff_is_unchanged_and_shorter():
    wait = _wait_for_retry(_FakeRetryState(TransientServerError("HTTP 503"), attempt_number=1))
    assert 0.0 <= wait <= 1.0


def test_429_raises_rate_limited_with_retry_after():
    with pytest.raises(RateLimitedError) as exc_info:
        raise_for_retryable_status(_response(429, {"Retry-After": "12"}))
    assert exc_info.value.retry_after == 12.0


def test_429_without_retry_after_header_still_raises():
    with pytest.raises(RateLimitedError) as exc_info:
        raise_for_retryable_status(_response(429))
    assert exc_info.value.retry_after is None


def test_5xx_raises_transient_server_error():
    with pytest.raises(TransientServerError):
        raise_for_retryable_status(_response(503))


def test_2xx_and_non_429_4xx_are_not_retryable():
    raise_for_retryable_status(_response(200))
    raise_for_retryable_status(_response(404))  # no exception


def test_circuit_breaker_opens_after_threshold_and_recovers_on_success():
    breaker = CircuitBreaker(failure_threshold=3)
    breaker.raise_if_open()  # closed initially

    breaker.record_failure()
    breaker.record_failure()
    breaker.raise_if_open()  # still under threshold

    breaker.record_failure()
    with pytest.raises(CircuitOpenError):
        breaker.raise_if_open()

    breaker.record_success()
    breaker.raise_if_open()  # closed again


def test_circuit_breaker_recovers_after_cooldown(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("packages.source_clients.retry.time.monotonic", lambda: now[0])
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0)

    breaker.record_failure()
    breaker.record_failure()
    with pytest.raises(CircuitOpenError) as exc_info:
        breaker.raise_if_open()
    assert exc_info.value.retry_after == pytest.approx(30.0)

    now[0] = 129.0
    with pytest.raises(CircuitOpenError) as exc_info:
        breaker.raise_if_open()
    assert exc_info.value.retry_after == pytest.approx(1.0)

    now[0] = 130.0
    breaker.raise_if_open()

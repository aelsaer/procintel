import time

import pytest

from packages.source_clients.rate_limit import TokenBucket


async def test_acquire_within_burst_capacity_does_not_block():
    bucket = TokenBucket(rate_per_minute=600, burst=5)  # 10 req/sec, burst 5
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.2


async def test_acquire_beyond_burst_capacity_waits():
    bucket = TokenBucket(rate_per_minute=600, burst=2)  # 10 req/sec, burst 2
    start = time.monotonic()
    for _ in range(3):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    # third acquire must wait roughly 1/10s for a token to regenerate
    assert elapsed >= 0.08


def test_zero_or_negative_rate_is_rejected():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_minute=0)


async def test_cost_larger_than_capacity_is_rejected():
    bucket = TokenBucket(rate_per_minute=60, burst=1)
    with pytest.raises(ValueError):
        await bucket.acquire(cost=2)

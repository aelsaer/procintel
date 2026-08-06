from __future__ import annotations

from packages.source_clients.shared_rate_limit import SharedSlidingWindowLimiter


def test_shared_limiter_combines_budget_across_instances(tmp_path) -> None:
    now = [1000.0]
    path = tmp_path / "provider.json"
    first = SharedSlidingWindowLimiter(2, str(path), clock=lambda: now[0])
    second = SharedSlidingWindowLimiter(2, str(path), clock=lambda: now[0])

    assert first._reserve_or_wait() == 0
    assert second._reserve_or_wait() == 0
    assert first._reserve_or_wait() == 60

    now[0] += 61
    assert second._reserve_or_wait() == 0


def test_shared_limiter_recovers_from_invalid_state(tmp_path) -> None:
    path = tmp_path / "provider.json"
    path.write_text('{"invalid": true}', encoding="utf-8")
    limiter = SharedSlidingWindowLimiter(1, str(path), clock=lambda: 1000.0)

    assert limiter._reserve_or_wait() == 0

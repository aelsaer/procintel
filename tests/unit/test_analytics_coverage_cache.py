from unittest.mock import AsyncMock

import pytest

from apps.api.routers import analytics


@pytest.mark.asyncio
async def test_data_coverage_cache_reuses_and_expires_results(monkeypatch):
    first = object()
    second = object()
    builder = AsyncMock(return_value=first)
    monkeypatch.setattr(analytics, "_build_data_coverage", builder)
    monkeypatch.setattr(analytics, "_data_coverage_cache", None)
    monkeypatch.setattr(analytics, "_data_coverage_refresh_task", None)
    monkeypatch.setenv("DATA_COVERAGE_CACHE_SECONDS", "30")

    assert await analytics.data_coverage(object()) is first
    assert await analytics.data_coverage(object()) is first
    assert builder.await_count == 1

    cached_at, value = analytics._data_coverage_cache
    monkeypatch.setattr(
        analytics,
        "_data_coverage_cache",
        (cached_at - 31, value),
    )
    async def refresh():
        analytics._data_coverage_cache = (analytics.time.monotonic(), second)

    monkeypatch.setattr(analytics, "_refresh_data_coverage_cache", refresh)
    assert await analytics.data_coverage(object()) is first
    await analytics._data_coverage_refresh_task
    assert await analytics.data_coverage(object()) is second
    assert builder.await_count == 1

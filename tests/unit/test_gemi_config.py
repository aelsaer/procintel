from __future__ import annotations

import pytest

from services.ingestion.connectors.gemi.config import (
    MAX_GEMI_RATE_LIMIT_PER_MINUTE,
    GemiConnectorConfig,
)


def test_gemi_config_uses_official_limit_and_shared_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEMI_API_KEY", "secret-for-test")
    monkeypatch.setenv("GEMI_API_BASE_URL", "")
    monkeypatch.setenv("RAW_STORE_ROOT", str(tmp_path))
    monkeypatch.delenv("GEMI_RATE_LIMIT_PER_MINUTE", raising=False)

    config = GemiConnectorConfig.from_env()

    assert config.rate_limit_per_minute == MAX_GEMI_RATE_LIMIT_PER_MINUTE == 8
    assert config.base_url == "https://opendata-api.businessportal.gr/api/opendata/v1"
    assert config.max_retry_attempts == 3
    assert config.rate_limit_state_path == str(tmp_path / "provider-limits" / "gemi.json")


def test_gemi_config_rejects_rate_above_provider_limit(monkeypatch) -> None:
    monkeypatch.setenv("GEMI_API_KEY", "secret-for-test")
    monkeypatch.setenv("GEMI_RATE_LIMIT_PER_MINUTE", "9")

    with pytest.raises(RuntimeError, match="must not exceed the provider limit of 8"):
        GemiConnectorConfig.from_env()


def test_gemi_config_rejects_blank_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMI_API_KEY", "   ")

    with pytest.raises(RuntimeError, match="GEMI_API_KEY is not set"):
        GemiConnectorConfig.from_env()

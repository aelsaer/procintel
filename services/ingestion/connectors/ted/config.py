"""Configuration for the public TED Search API v3."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TED_API_BASE_URL = "https://api.ted.europa.eu"


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class TedConnectorConfig:
    base_url: str
    rate_limit_per_minute: float = 120.0  # no official number published; conservative default
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0
    rate_limit_state_path: str | None = None

    @classmethod
    def from_env(cls) -> "TedConnectorConfig":
        base_url = (os.environ.get("TED_API_BASE_URL") or DEFAULT_TED_API_BASE_URL).strip()
        raw_root = os.environ.get("RAW_STORE_ROOT", "./raw")
        return cls(
            base_url=base_url,
            rate_limit_per_minute=_float_env("TED_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
            rate_limit_state_path=os.environ.get("TED_RATE_LIMIT_STATE_PATH")
            or str(Path(raw_root) / "provider-limits" / "ted.json"),
        )

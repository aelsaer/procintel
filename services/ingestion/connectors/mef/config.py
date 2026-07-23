"""Configuration for the public ΜΕΦ Open Data API."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_MEF_API_BASE_URL = "https://mef.diavgeia.gov.gr"


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
class MefConnectorConfig:
    base_url: str
    rate_limit_per_minute: float = 60.0  # no official number published; conservative default
    page_size: int = 200
    max_pages_per_lookup: int = 5
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "MefConnectorConfig":
        base_url = os.environ.get("MEF_API_BASE_URL", DEFAULT_MEF_API_BASE_URL)
        return cls(
            base_url=base_url,
            rate_limit_per_minute=_float_env("MEF_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
        )

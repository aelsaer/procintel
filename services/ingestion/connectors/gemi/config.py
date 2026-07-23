"""Configuration for the published ΓΕΜΗ Open Data v1 contract."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_GEMI_API_BASE_URL = "https://opendata-api.businessportal.gr/api/opendata/v1"


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
class GemiConnectorConfig:
    base_url: str
    api_key: str
    rate_limit_per_minute: float = 60.0  # no official number published; conservative default
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "GemiConnectorConfig":
        base_url = os.environ.get("GEMI_API_BASE_URL", DEFAULT_GEMI_API_BASE_URL)
        api_key = os.environ.get("GEMI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMI_API_KEY is not set. Request a key from the ΓΕΜΗ Open Data "
                "service; GEMI_API_BASE_URL only needs to be set for an alternate deployment."
            )
        return cls(
            base_url=base_url,
            api_key=api_key,
            rate_limit_per_minute=_float_env("GEMI_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
        )

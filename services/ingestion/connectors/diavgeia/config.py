"""Connector configuration.

The production Open Data base URL is documented by Διαύγεια's API help page.
`DIAVGEIA_API_BASE_URL` remains an override for staging/proxy use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DIAVGEIA_API_BASE_URL = "https://diavgeia.gov.gr/opendata"


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
class DiavgeiaConnectorConfig:
    base_url: str = DEFAULT_DIAVGEIA_API_BASE_URL
    rate_limit_per_minute: float = 120.0  # no official number published; conservative default
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "DiavgeiaConnectorConfig":
        return cls(
            base_url=os.environ.get("DIAVGEIA_API_BASE_URL", DEFAULT_DIAVGEIA_API_BASE_URL),
            rate_limit_per_minute=_float_env("DIAVGEIA_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
        )

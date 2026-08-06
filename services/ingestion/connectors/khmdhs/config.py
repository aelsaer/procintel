"""Connector configuration.

The production Open Data base URL is documented in the official ΚΗΜΔΗΣ API
help page. `KHMDHS_API_BASE_URL` remains an override for staging/proxy use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_KHMDHS_API_BASE_URL = "https://cerpp.eprocurement.gov.gr"


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
class KhmdhsConnectorConfig:
    base_url: str = DEFAULT_KHMDHS_API_BASE_URL
    rate_limit_per_minute: float = 210.0  # inside the 180-240 target band, §16.3
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "KhmdhsConnectorConfig":
        return cls(
            base_url=(os.environ.get("KHMDHS_API_BASE_URL") or DEFAULT_KHMDHS_API_BASE_URL).strip(),
            rate_limit_per_minute=_float_env("KHMDHS_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
        )

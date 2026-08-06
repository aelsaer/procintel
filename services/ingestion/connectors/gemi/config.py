"""Configuration for the published ΓΕΜΗ Open Data v1 contract."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_GEMI_API_BASE_URL = "https://opendata-api.businessportal.gr/api/opendata/v1"
MAX_GEMI_RATE_LIMIT_PER_MINUTE = 8.0


def _float_env(name: str, default: float, *, maximum: float | None = None) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} must not exceed the provider limit of {maximum:g}")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class GemiConnectorConfig:
    base_url: str
    api_key: str
    rate_limit_per_minute: float = MAX_GEMI_RATE_LIMIT_PER_MINUTE
    max_retry_attempts: int = 3
    request_timeout_seconds: float = 30.0
    rate_limit_state_path: str | None = None

    @classmethod
    def from_env(cls) -> "GemiConnectorConfig":
        base_url = os.environ.get("GEMI_API_BASE_URL") or DEFAULT_GEMI_API_BASE_URL
        api_key = (os.environ.get("GEMI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError(
                "GEMI_API_KEY is not set. Request a key from the ΓΕΜΗ Open Data "
                "service; GEMI_API_BASE_URL only needs to be set for an alternate deployment."
            )
        raw_root = os.environ.get("RAW_STORE_ROOT", "./raw")
        return cls(
            base_url=base_url,
            api_key=api_key,
            rate_limit_per_minute=_float_env(
                "GEMI_RATE_LIMIT_PER_MINUTE",
                cls.rate_limit_per_minute,
                maximum=MAX_GEMI_RATE_LIMIT_PER_MINUTE,
            ),
            max_retry_attempts=_positive_int_env(
                "GEMI_MAX_RETRY_ATTEMPTS",
                cls.max_retry_attempts,
            ),
            rate_limit_state_path=os.environ.get("GEMI_RATE_LIMIT_STATE_PATH")
            or str(Path(raw_root) / "provider-limits" / "gemi.json"),
        )

"""Configuration for the public ΜΕΦ Open Data API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone


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


def _int_env(name: str, default: int) -> int:
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


def _years_env(name: str) -> tuple[int, ...]:
    raw = os.environ.get(name)
    if not raw:
        return (datetime.now(timezone.utc).year,)
    try:
        years = tuple(
            sorted(
                {
                    int(item.strip())
                    for item in raw.split(",")
                    if item.strip()
                },
                reverse=True,
            )
        )
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a comma-separated year list") from exc
    if not years or any(year < 2000 or year > 2100 for year in years):
        raise RuntimeError(f"{name} contains an invalid year")
    return years


@dataclass(frozen=True)
class MefConnectorConfig:
    base_url: str
    rate_limit_per_minute: float = 60.0  # no official number published; conservative default
    page_size: int = 200
    max_pages_per_lookup: int = 5
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0
    lookup_years: tuple[int, ...] = ()

    @classmethod
    def from_env(cls) -> "MefConnectorConfig":
        base_url = (os.environ.get("MEF_API_BASE_URL") or DEFAULT_MEF_API_BASE_URL).strip()
        return cls(
            base_url=base_url,
            rate_limit_per_minute=_float_env("MEF_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
            max_retry_attempts=_int_env(
                "MEF_MAX_RETRY_ATTEMPTS", cls.max_retry_attempts
            ),
            request_timeout_seconds=_float_env(
                "MEF_REQUEST_TIMEOUT_SECONDS", cls.request_timeout_seconds
            ),
            lookup_years=_years_env("MEF_LOOKUP_YEARS"),
        )

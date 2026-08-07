"""Connector configuration for the live-validated data.gov.gr CKAN API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class CkanConnectorConfig:
    base_url: str
    rate_limit_per_minute: float = 60.0  # no official number published; conservative default
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 60.0  # resource downloads can be larger than API calls
    rate_limit_state_path: str | None = None

    @classmethod
    def from_env(cls) -> "CkanConnectorConfig":
        base_url = os.environ.get("CKAN_API_BASE_URL") or "https://data.gov.gr"
        raw_root = os.environ.get("RAW_STORE_ROOT", "./raw")
        return cls(
            base_url=base_url,
            rate_limit_per_minute=_positive_float_env(
                "CKAN_RATE_LIMIT_PER_MINUTE",
                cls.rate_limit_per_minute,
            ),
            rate_limit_state_path=os.environ.get("CKAN_RATE_LIMIT_STATE_PATH")
            or str(Path(raw_root) / "provider-limits" / "ckan.json"),
        )

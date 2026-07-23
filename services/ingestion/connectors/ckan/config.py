"""Connector configuration.

`base_url` has no default — description.txt (§3.6, §22) confirms
data.gov.gr is CKAN-based with a generic Action API (`package_search`,
`package_show`, `resource_search`) but explicitly says the exact paths and
limits must be confirmed against the live deployment, same discipline as
every other connector.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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

    @classmethod
    def from_env(cls) -> "CkanConnectorConfig":
        base_url = os.environ.get("CKAN_API_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "CKAN_API_BASE_URL is not set. See docs/source-contracts/ckan-datagov.md — "
                "confirm the base hostname against the live data.gov.gr deployment before "
                "setting this."
            )
        return cls(
            base_url=base_url,
            rate_limit_per_minute=_positive_float_env(
                "CKAN_RATE_LIMIT_PER_MINUTE",
                cls.rate_limit_per_minute,
            ),
        )

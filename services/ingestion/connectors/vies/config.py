"""Connector configuration.

`base_url` has no default — description.txt confirms VIES exposes a WSDL
`checkVat` operation (§3.9) but this repo doesn't assert the real endpoint
without the spec stating it, same discipline as every other connector.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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
class ViesConnectorConfig:
    base_url: str
    rate_limit_per_minute: float = 60.0
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "ViesConnectorConfig":
        base_url = os.environ.get("VIES_API_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "VIES_API_BASE_URL is not set. See docs/source-contracts/vies.md — "
                "confirm the checkVat SOAP endpoint against the live WSDL before setting this."
            )
        return cls(
            base_url=base_url,
            rate_limit_per_minute=_float_env("VIES_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
        )

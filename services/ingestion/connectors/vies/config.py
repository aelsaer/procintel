"""Connector configuration for the European Commission VIES SOAP service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://ec.europa.eu/taxation_customs/vies/services"


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
    rate_limit_state_path: str | None = None

    @classmethod
    def from_env(cls) -> "ViesConnectorConfig":
        raw_root = os.environ.get("RAW_STORE_ROOT", "./raw")
        return cls(
            base_url=(os.environ.get("VIES_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            rate_limit_per_minute=_float_env("VIES_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
            rate_limit_state_path=os.environ.get("VIES_RATE_LIMIT_STATE_PATH")
            or str(Path(raw_root) / "provider-limits" / "vies.json"),
        )

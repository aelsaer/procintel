"""Configuration for an optional Nominatim-compatible geocoder.

No public service is enabled implicitly. Production/bulk workloads should use
a self-hosted or contracted provider. If the operator explicitly selects the
public OSMF endpoint, the worker enforces its periodic-job ceiling of four
requests per minute and always uses the database cache.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

PUBLIC_NOMINATIM_HOST = "nominatim.openstreetmap.org"


def _positive_float(name: str, default: float) -> float:
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
class GeocoderConfig:
    base_url: str
    user_agent: str
    rate_limit_per_minute: float = 60.0
    timeout_seconds: float = 20.0
    provider_name: str = "NOMINATIM"

    @property
    def is_public_osmf(self) -> bool:
        return (urlparse(self.base_url).hostname or "").lower() == PUBLIC_NOMINATIM_HOST

    @property
    def effective_rate_limit_per_minute(self) -> float:
        return min(self.rate_limit_per_minute, 4.0) if self.is_public_osmf else self.rate_limit_per_minute

    @classmethod
    def from_env(cls) -> GeocoderConfig | None:
        base_url = os.environ.get("GEO_GEOCODER_BASE_URL", "").strip()
        if not base_url:
            return None
        user_agent = os.environ.get("GEO_GEOCODER_USER_AGENT", "").strip()
        if not user_agent:
            raise RuntimeError(
                "GEO_GEOCODER_USER_AGENT is required when GEO_GEOCODER_BASE_URL is set; "
                "use an identifying application/contact value accepted by your provider"
            )
        return cls(
            base_url=base_url.rstrip("/"),
            user_agent=user_agent,
            rate_limit_per_minute=_positive_float("GEO_GEOCODER_RATE_LIMIT_PER_MINUTE", 60.0),
            timeout_seconds=_positive_float("GEO_GEOCODER_TIMEOUT_SECONDS", 20.0),
            provider_name=os.environ.get("GEO_GEOCODER_PROVIDER", "NOMINATIM").strip().upper(),
        )

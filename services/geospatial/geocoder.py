"""Local administrative-boundary and optional Nominatim geocoders."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from .config import GeocoderConfig
from .extract import AdminUnit, LocationCandidate, normalize_place


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str
    municipality_name: str | None
    regional_unit_name: str | None
    region_name: str | None
    postal_code: str | None
    country_code: str
    precision: str
    provider: str
    confidence: float
    raw_response: dict[str, Any] | None = None


def _unit_aliases(unit: AdminUnit) -> set[str]:
    normalized = normalize_place(unit.name)
    aliases = {normalized}
    for prefix in ("ΔΗΜΟΣ ", "ΠΕΡΙΦΕΡΕΙΑΚΗ ΕΝΟΤΗΤΑ ", "ΠΕΡΙΦΕΡΕΙΑ ", "ΝΟΜΟΣ "):
        if normalized.startswith(prefix):
            aliases.add(normalized[len(prefix) :])
    return aliases


def match_local_boundary(candidate: LocationCandidate, units: Sequence[AdminUnit]) -> GeocodeResult | None:
    query = normalize_place(candidate.place_text)
    ranked: list[tuple[int, AdminUnit]] = []
    for unit in units:
        exact_nuts = bool(
            unit.nuts_code and unit.nuts_code in candidate.nuts_codes
        )
        if unit.nuts_code and candidate.nuts_codes and not any(
            unit.nuts_code.startswith(code) or code.startswith(unit.nuts_code) for code in candidate.nuts_codes
        ):
            continue
        aliases = _unit_aliases(unit)
        if query in aliases:
            rank = 100
        elif len(query) >= 5 and any(query in alias or alias in query for alias in aliases):
            rank = 80
        elif exact_nuts:
            rank = 90
        else:
            continue
        if candidate.granularity_hint == unit.boundary_type:
            rank += 10
        ranked.append((rank, unit))
    if not ranked:
        return None
    rank, unit = max(ranked, key=lambda item: item[0])
    kind = unit.boundary_type.upper()
    return GeocodeResult(
        latitude=unit.latitude,
        longitude=unit.longitude,
        display_name=unit.name,
        municipality_name=unit.name if kind == "MUNICIPALITY" else None,
        regional_unit_name=unit.name if kind in {"REGIONAL_UNIT", "PREFECTURE"} else None,
        region_name=unit.name if kind == "REGION" else None,
        postal_code=candidate.postal_code,
        country_code="GR",
        precision=kind,
        provider="LOCAL_BOUNDARY",
        confidence=min(candidate.confidence, 0.98 if rank >= 100 else 0.88),
    )


class NominatimGeocoder:
    def __init__(self, config: GeocoderConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _wait_for_slot(self) -> None:
        minimum_interval = 60.0 / self.config.effective_rate_limit_per_minute
        async with self._lock:
            wait = minimum_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def geocode(self, candidate: LocationCandidate) -> GeocodeResult | None:
        await self._wait_for_slot()
        query_parts = [candidate.place_text]
        if candidate.postal_code:
            query_parts.append(candidate.postal_code)
        query_parts.append("Greece")
        response = await self._client.get(
            f"{self.config.base_url}/search",
            params={
                "q": ", ".join(query_parts),
                "format": "jsonv2",
                "addressdetails": 1,
                "countrycodes": "gr",
                "limit": 5,
            },
            headers={"User-Agent": self.config.user_agent, "Accept-Language": "el,en"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("geocoder returned a non-list response")

        for item in payload:
            if not isinstance(item, dict):
                continue
            address = item.get("address") if isinstance(item.get("address"), dict) else {}
            country_code = str(address.get("country_code") or "").upper()
            if country_code and country_code != "GR":
                continue
            municipality = (
                address.get("municipality")
                or address.get("city")
                or address.get("town")
                or address.get("village")
            )
            regional_unit = address.get("county") or address.get("state_district")
            region = address.get("state") or address.get("region")
            precision = str(item.get("type") or item.get("category") or "POINT").upper()
            try:
                latitude = float(item["lat"])
                longitude = float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            return GeocodeResult(
                latitude=latitude,
                longitude=longitude,
                display_name=str(item.get("display_name") or candidate.place_text),
                municipality_name=str(municipality) if municipality else None,
                regional_unit_name=str(regional_unit) if regional_unit else None,
                region_name=str(region) if region else None,
                postal_code=str(address.get("postcode") or candidate.postal_code or "") or None,
                country_code=country_code or "GR",
                precision=precision,
                provider=self.config.provider_name,
                confidence=min(candidate.confidence, 0.92),
                raw_response=item,
            )
        return None

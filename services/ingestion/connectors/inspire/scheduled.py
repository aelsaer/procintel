"""Refresh auditable INSPIRE capabilities and Greek NUTS reference data."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.source_clients.raw_store import LocalFilesystemRawStore

from .capabilities import CapabilityCheckResult, validate_wms_service
from .config import InspireReferenceConfig
from .nuts import NutsLoadResult, load_greece_nuts
from .postal import PostalNutsLoadResult, load_greece_postal_nuts


@dataclass(frozen=True)
class InspireRefreshResult:
    ktimatologio: CapabilityCheckResult
    nuts: NutsLoadResult
    postal_nuts: PostalNutsLoadResult


async def refresh_inspire_reference_sources(
    conn: AsyncConnection,
    *,
    raw_root: str,
    config: InspireReferenceConfig | None = None,
) -> InspireRefreshResult:
    config = config or InspireReferenceConfig.from_env()
    raw_store = LocalFilesystemRawStore(raw_root)
    async with httpx.AsyncClient(
        timeout=config.request_timeout_seconds,
        headers={"User-Agent": "Procintel/0.1 INSPIRE reference loader"},
    ) as client:
        ktimatologio = await validate_wms_service(
            conn,
            http_client=client,
            raw_store=raw_store,
            service_url=config.ktimatologio_wms_url,
        )
        nuts = await load_greece_nuts(
            conn,
            http_client=client,
            raw_store=raw_store,
            url=config.greece_nuts_url,
        )
        postal_nuts = await load_greece_postal_nuts(
            conn,
            http_client=client,
            raw_store=raw_store,
            url=config.greece_postal_nuts_url,
        )
    return InspireRefreshResult(
        ktimatologio=ktimatologio,
        nuts=nuts,
        postal_nuts=postal_nuts,
    )

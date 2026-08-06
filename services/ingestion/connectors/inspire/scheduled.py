"""Refresh auditable INSPIRE capabilities and Greek NUTS reference data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.source_clients.raw_store import LocalFilesystemRawStore

from .capabilities import CapabilityCheckResult, validate_wms_service
from .config import InspireReferenceConfig
from .csw import CswDiscoveryResult, discover_and_validate_csw_services
from .nuts import NutsLoadResult, load_greece_nuts
from .postal import PostalNutsLoadResult, load_greece_postal_nuts
from .selected_layers import (
    SelectedLayerRefreshResult,
    refresh_selected_inspire_layers,
)


@dataclass(frozen=True)
class InspireRefreshResult:
    ktimatologio: CapabilityCheckResult
    catalog: CswDiscoveryResult
    selected_layers: tuple[SelectedLayerRefreshResult, ...]
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
        follow_redirects=True,
        headers={"User-Agent": "Procintel/0.1 INSPIRE reference loader"},
    ) as client:
        ktimatologio = await validate_wms_service(
            conn,
            http_client=client,
            raw_store=raw_store,
            service_url=config.ktimatologio_wms_url,
            blocked_retry_after=timedelta(days=config.blocked_retry_days),
        )
        try:
            catalog = await discover_and_validate_csw_services(
                conn,
                http_client=client,
                raw_store=raw_store,
                csw_url=config.greek_inspire_csw_url,
                max_records=config.csw_max_records,
                max_service_checks=config.csw_max_service_checks,
                blocked_retry_after=timedelta(days=config.blocked_retry_days),
            )
        except (httpx.HTTPError, ValueError) as exc:
            await conn.rollback()
            catalog = CswDiscoveryResult(
                records_seen=0,
                services_discovered=0,
                services_checked=0,
                services_skipped=0,
                available=0,
                degraded=0,
                blocked=0,
                invalid=0,
                error=f"{type(exc).__name__}: {exc}",
            )
        selected_layers = await refresh_selected_inspire_layers(
            conn,
            http_client=client,
            raw_store=raw_store,
            blocked_retry_after=timedelta(days=config.blocked_retry_days),
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
        catalog=catalog,
        selected_layers=selected_layers,
        nuts=nuts,
        postal_nuts=postal_nuts,
    )

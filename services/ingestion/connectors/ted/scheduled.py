"""TED scheduled ingestion, process matching and optional VIES validation.

`country` defaults to `GR` for the same reason `cli.py`'s `--country`
flag does: the platform's whole reason for touching TED is Greek
contracts published EU-wide (§21).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.vies.client import ViesClient
from services.ingestion.connectors.vies.config import ViesConnectorConfig
from services.ingestion.connectors.vies.resolve import check_and_record_vies
from services.search_index.config import OpenSearchConfig
from services.search_index.indexer import index_single_act

from .client import TedClient
from .config import TedConnectorConfig
from .db_writer import TedIngestResult
from .pipeline import ingest_ted_partition
from .resolve import resolve_notice_process_link

_logger = logging.getLogger("procintel.ted.scheduled")


async def run_scheduled_window(
    conn: AsyncConnection,
    date_from: date,
    date_to: date,
    *,
    country: str = "GR",
    raw_root: str = "./raw",
    vies_config: ViesConnectorConfig | None = None,
    vies_lookup_budget: int | None = None,
    opensearch_config: OpenSearchConfig | None = None,
) -> dict[str, Any]:
    client = TedClient(TedConnectorConfig.from_env())
    vies_client = ViesClient(vies_config) if vies_config is not None else None
    opensearch_http_client = (
        httpx.AsyncClient(timeout=opensearch_config.request_timeout_seconds)
        if opensearch_config is not None
        else None
    )
    raw_store = LocalFilesystemRawStore(raw_root)
    enrichment_attempts: dict[str, int] = {}
    enrichment_succeeded: dict[str, int] = {}
    enrichment_failures: list[dict[str, str]] = []
    enrichment_deferred = 0
    attempted_vies_vats: set[tuple[str, str]] = set()

    async def _attempt(provider: str, notice_id: str, operation):
        enrichment_attempts[provider] = enrichment_attempts.get(provider, 0) + 1
        try:
            value = await operation()
        except Exception as exc:  # noqa: BLE001 - process matching and VIES are independent
            enrichment_failures.append(
                {
                    "provider": provider,
                    "notice_id": notice_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return None, False
        enrichment_succeeded[provider] = enrichment_succeeded.get(provider, 0) + 1
        return value, True

    async def _on_notice_upserted(inner_conn: AsyncConnection, result: TedIngestResult) -> None:
        nonlocal enrichment_deferred
        notice = result.notice
        if notice is None:
            return
        notice_label = str(notice.act_id)
        await _attempt(
            "TED_PROCESS_MATCHING",
            notice_label,
            lambda: resolve_notice_process_link(
                inner_conn,
                ted_act_id=notice.act_id,
                buyer_entity_id=notice.buyer_entity_id,
                cpv_codes=notice.cpv_codes,
                publication_date=notice.publication_date,
                title=notice.title,
                amount=notice.amount,
            ),
        )
        if opensearch_http_client is not None and opensearch_config is not None:
            await _attempt(
                "OPENSEARCH",
                notice_label,
                lambda: index_single_act(
                    inner_conn,
                    opensearch_http_client,
                    opensearch_config,
                    notice.act_id,
                ),
            )
        if (
            vies_client is not None
            and notice.supplier_entity_id is not None
            and notice.supplier_country_code
            and notice.supplier_country_code != "GR"
            and notice.supplier_vat
            and (notice.supplier_country_code, notice.supplier_vat) not in attempted_vies_vats
        ):
            if vies_lookup_budget is not None and enrichment_attempts.get("VIES", 0) >= vies_lookup_budget:
                enrichment_deferred += 1
                return
            attempted_vies_vats.add((notice.supplier_country_code, notice.supplier_vat))
            await _attempt(
                "VIES",
                notice_label,
                lambda: check_and_record_vies(
                    inner_conn,
                    client=vies_client,
                    entity_id=notice.supplier_entity_id,
                    country_code=notice.supplier_country_code,
                    vat_number=notice.supplier_vat,
                ),
            )

    try:
        result = await ingest_ted_partition(
            client=client,
            raw_store=raw_store,
            conn=conn,
            country=country,
            date_from=date_from,
            date_to=date_to,
            on_notice_upserted=_on_notice_upserted,
            enrich_deduplicated=True,
        )
    finally:
        await client.aclose()
        if vies_client is not None:
            await vies_client.aclose()
        if opensearch_http_client is not None:
            await opensearch_http_client.aclose()

    return {
        "pages_fetched": result.pages_fetched,
        "records_fetched": result.notices_seen,
        "records_upserted": result.notices_ingested,
        "records_failed": result.notices_failed,
        "enrichment_attempts": enrichment_attempts,
        "enrichment_succeeded": enrichment_succeeded,
        "enrichment_failed": len(enrichment_failures),
        "enrichment_deferred": {"VIES": enrichment_deferred} if enrichment_deferred else {},
        "enrichment_failures": enrichment_failures[:50],
    }

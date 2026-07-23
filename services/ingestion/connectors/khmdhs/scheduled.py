"""ΚΗΜΔΗΣ scheduled ingestion plus targeted cross-source enrichment.

ΚΗΜΔΗΣ is the primary incremental feed. Every newly written procurement
record is then enriched through every configured provider: Διαύγεια by
related ΑΔΑ (with a targeted search fallback), ΓΕΜΗ and ΜΕΦ by contractor
ΑΦΜ, and each configured ΑΝΑΠΤΥΞΗ programming period by funding references.
Provider calls keep their own conservative rate limit/retry policies.

Enrichment failures are isolated and reported as partial results: an outage
at one public API does not discard the canonical ΚΗΜΔΗΣ record or prevent
the other providers from running. Content hashes and canonical upserts keep
the rolling daily overlap idempotent.

`run_scheduled_window` is the `RunWindow` callable
`services/ingestion/orchestration/jobs.py::default_jobs()` hands to
`scheduler.ScheduledJob` — it owns its own `KhmdhsClient` lifecycle (opened
and closed within one call), since the scheduler invokes it once per due
window, not continuously.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_identifiers,
    act_links,
    act_locations,
    act_parties,
    entities,
    entity_identifiers,
    procurement_acts,
)
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.alerts.delivery import DeliveryChannel
from services.alerts.evaluate import evaluate_and_fire, evaluate_company_status_change_and_fire
from services.alerts.factory import build_delivery_channel
from services.ingestion.connectors.anaptyxi.client import AnaptyxiClient
from services.ingestion.connectors.anaptyxi.config import AnaptyxiConnectorConfig
from services.ingestion.connectors.anaptyxi.resolve import resolve_funding_link_for_act
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.resolve import (
    link_existing_decision_for_ada,
    resolve_decision_for_ada,
    resolve_decision_via_search,
)
from services.ingestion.connectors.gemi.client import GemiClient
from services.ingestion.connectors.gemi.config import GemiConnectorConfig
from services.ingestion.connectors.gemi.provider import GemiCompanyRegistryProvider
from services.ingestion.connectors.gemi.resolve import resolve_company_snapshot
from services.ingestion.connectors.mef.client import MefClient
from services.ingestion.connectors.mef.config import MefConnectorConfig
from services.ingestion.connectors.mef.resolve import resolve_expenses_for_contractor
from services.search_index.config import OpenSearchConfig
from services.search_index.indexer import index_single_act

from .adamchain import resolve_adam_chain_for_act
from .client import ALL_RESOURCES, KhmdhsClient
from .config import KhmdhsConnectorConfig
from .db_writer import IngestResult
from .documents import has_khmdhs_attachment, process_khmdhs_attachment
from .pipeline import ingest_khmdhs_partition

_logger = logging.getLogger("procintel.khmdhs.scheduled")
_MAX_RECORDED_ENRICHMENT_FAILURES = 50


async def _fetch_act_title_and_buyer_name(
    conn: AsyncConnection, act_id: Any
) -> tuple[str | None, str | None]:
    title = (
        await conn.execute(select(procurement_acts.c.title).where(procurement_acts.c.id == act_id))
    ).scalar()
    buyer_name = (
        await conn.execute(
            select(entities.c.canonical_name)
            .select_from(entities.join(act_parties, act_parties.c.entity_id == entities.c.id))
            .where(act_parties.c.act_id == act_id, act_parties.c.party_role == "BUYER")
        )
    ).scalar()
    return title, buyer_name


async def _fetch_act_details_for_anaptyxi(conn: AsyncConnection, act_id: Any) -> dict[str, Any]:
    act_row = (
        await conn.execute(
            select(
                procurement_acts.c.title,
                procurement_acts.c.decision_date,
                procurement_acts.c.submission_date,
                procurement_acts.c.publication_date,
                procurement_acts.c.amount_gross,
            ).where(procurement_acts.c.id == act_id)
        )
    ).first()
    act_date = None
    if act_row is not None:
        act_date = act_row.decision_date or act_row.submission_date or act_row.publication_date

    buyer_afm = (
        await conn.execute(
            select(entity_identifiers.c.value_normalized)
            .select_from(
                entity_identifiers.join(act_parties, act_parties.c.entity_id == entity_identifiers.c.entity_id)
            )
            .where(
                act_parties.c.act_id == act_id,
                act_parties.c.party_role == "BUYER",
                entity_identifiers.c.scheme == "AFM",
            )
        )
    ).scalar()
    region = (
        await conn.execute(select(act_locations.c.nuts_code).where(act_locations.c.act_id == act_id).limit(1))
    ).scalar()
    return {
        "title": act_row.title if act_row is not None else None,
        "date": act_date,
        "amount": act_row.amount_gross if act_row is not None else None,
        "buyer_afm": buyer_afm,
        "region": region,
    }


async def _has_diavgeia_link(conn: AsyncConnection, act_id: Any) -> bool:
    return (
        await conn.execute(
            select(act_links.c.id)
            .select_from(
                act_links.join(
                    act_identifiers,
                    act_identifiers.c.act_id == act_links.c.from_act_id,
                )
            )
            .where(
                act_links.c.to_act_id == act_id,
                act_links.c.link_type == "APPROVES",
                act_identifiers.c.scheme == "ADA",
            )
            .limit(1)
        )
    ).first() is not None


async def run_scheduled_window(
    conn: AsyncConnection,
    date_from: date,
    date_to: date,
    *,
    raw_root: str = "./raw",
    delivery_channel: DeliveryChannel | None = None,
    opensearch_config: OpenSearchConfig | None = None,
    diavgeia_config: DiavgeiaConnectorConfig | None = None,
    diavgeia_search: bool = True,
    gemi_config: GemiConnectorConfig | None = None,
    anaptyxi_configs: tuple[AnaptyxiConnectorConfig, ...] = (),
    mef_config: MefConnectorConfig | None = None,
    provider_lookup_budgets: dict[str, int] | None = None,
    process_documents: bool = False,
) -> dict[str, Any]:
    config = KhmdhsConnectorConfig.from_env()
    client = KhmdhsClient(config)
    raw_store = LocalFilesystemRawStore(raw_root)
    alert_http_client = httpx.AsyncClient(timeout=10.0) if delivery_channel is None else None
    delivery_channel = delivery_channel or build_delivery_channel(alert_http_client)
    opensearch_http_client = httpx.AsyncClient(timeout=10.0) if opensearch_config is not None else None
    diavgeia_client = DiavgeiaClient(diavgeia_config) if diavgeia_config is not None else None
    gemi_client = GemiClient(gemi_config) if gemi_config is not None else None
    gemi_provider = GemiCompanyRegistryProvider(gemi_client) if gemi_client is not None else None
    anaptyxi_clients = [AnaptyxiClient(config) for config in anaptyxi_configs]
    mef_client = MefClient(mef_config) if mef_config is not None else None
    attempted_diavgeia_adas: set[str] = set()
    attempted_gemi_afms: set[str] = set()
    attempted_mef_afms: set[str] = set()

    totals: dict[str, Any] = {
        "pages_fetched": 0,
        "records_fetched": 0,
        "records_upserted": 0,
        "records_failed": 0,
        "enrichment_attempts": {},
        "enrichment_succeeded": {},
        "enrichment_failed": 0,
        "enrichment_deferred": {},
        "enrichment_failures": [],
    }

    async def _attempt(provider: str, adam: str, operation):
        budget = (provider_lookup_budgets or {}).get(provider)
        attempted = totals["enrichment_attempts"].get(provider, 0)
        if budget is not None and attempted >= budget:
            totals["enrichment_deferred"][provider] = totals["enrichment_deferred"].get(provider, 0) + 1
            return None, False
        totals["enrichment_attempts"][provider] = totals["enrichment_attempts"].get(provider, 0) + 1
        try:
            value = await operation()
        except Exception as exc:  # noqa: BLE001 - each provider is an independent failure boundary
            totals["enrichment_failed"] += 1
            if len(totals["enrichment_failures"]) < _MAX_RECORDED_ENRICHMENT_FAILURES:
                totals["enrichment_failures"].append(
                    {
                        "provider": provider,
                        "adam": adam,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            _logger.exception("%s enrichment failed for %s", provider, adam)
            return None, False
        totals["enrichment_succeeded"][provider] = totals["enrichment_succeeded"].get(provider, 0) + 1
        return value, True

    async def _on_ingest_result(inner_conn: AsyncConnection, resource: str, result: IngestResult) -> None:
        if (
            process_documents
            and result.act_upsert is not None
            and not await has_khmdhs_attachment(
                inner_conn,
                act_id=result.act_upsert.act_id,
                resource=resource,
            )
        ):
            await _attempt(
                "KHMDHS_DOCUMENT",
                result.adam_normalized,
                lambda: process_khmdhs_attachment(
                    inner_conn,
                    resource=resource,
                    adam=result.adam_normalized,
                    act_id=result.act_upsert.act_id,
                ),
            )

        await _attempt(
            "KHMDHS_ADAMCHAIN",
            result.adam_normalized,
            lambda: resolve_adam_chain_for_act(
                inner_conn,
                client=client,
                raw_store=raw_store,
                seed_adam_normalized=result.adam_normalized,
                delivery_channel=delivery_channel,
            ),
        )
        if result.act_upsert is not None:
            await _attempt(
                "ALERTS",
                result.adam_normalized,
                lambda: evaluate_and_fire(
                    inner_conn,
                    act_upsert=result.act_upsert,
                    delivery_channel=delivery_channel,
                ),
            )

        if diavgeia_client is not None and result.act_upsert is not None:
            any_direct_link = await _has_diavgeia_link(inner_conn, result.act_upsert.act_id)
            if not any_direct_link:
                for ada in result.act_upsert.related_ada:
                    existing_decision = await link_existing_decision_for_ada(
                        inner_conn,
                        ada=ada,
                        origin_act_id=result.act_upsert.act_id,
                    )
                    if existing_decision is not None:
                        any_direct_link = True
                        continue
                    if ada in attempted_diavgeia_adas:
                        continue
                    attempted_diavgeia_adas.add(ada)
                    decision_act_id, succeeded = await _attempt(
                        "DIAVGEIA",
                        result.adam_normalized,
                        lambda ada=ada: resolve_decision_for_ada(
                            inner_conn,
                            client=diavgeia_client,
                            raw_store=raw_store,
                            ada=ada,
                            origin_act_id=result.act_upsert.act_id,
                            process_documents=False,
                        ),
                    )
                    any_direct_link = any_direct_link or (succeeded and decision_act_id is not None)
            if diavgeia_search and not any_direct_link:
                title, buyer_name = await _fetch_act_title_and_buyer_name(inner_conn, result.act_upsert.act_id)
                if title and buyer_name:
                    await _attempt(
                        "DIAVGEIA_SEARCH",
                        result.adam_normalized,
                        lambda: resolve_decision_via_search(
                            inner_conn,
                            client=diavgeia_client,
                            raw_store=raw_store,
                            origin_act_id=result.act_upsert.act_id,
                            organization_query=buyer_name,
                            title_query=title,
                            process_documents=False,
                        ),
                    )

        if (
            gemi_provider is not None
            and result.act_upsert is not None
            and result.act_upsert.contractor_entity_id is not None
            and result.act_upsert.contractor_afm_normalized
            and result.act_upsert.contractor_afm_normalized not in attempted_gemi_afms
        ):
            attempted_gemi_afms.add(result.act_upsert.contractor_afm_normalized)
            snapshot_result, succeeded = await _attempt(
                "GEMI",
                result.adam_normalized,
                lambda: resolve_company_snapshot(
                    inner_conn,
                    provider=gemi_provider,
                    raw_store=raw_store,
                    afm_normalized=result.act_upsert.contractor_afm_normalized,
                    entity_id=result.act_upsert.contractor_entity_id,
                ),
            )
            if succeeded and snapshot_result is not None and snapshot_result.wrote_new_snapshot:
                await _attempt(
                    "COMPANY_STATUS_ALERTS",
                    result.adam_normalized,
                    lambda: evaluate_company_status_change_and_fire(
                        inner_conn,
                        entity_id=result.act_upsert.contractor_entity_id,
                        old_status=snapshot_result.old_status,
                        new_status=snapshot_result.new_status,
                        delivery_channel=delivery_channel,
                    ),
                )

        if anaptyxi_clients and result.act_upsert is not None and (
            result.act_upsert.funding_ref_candidates or result.act_upsert.contractor_afm_normalized
        ):
            act_details = await _fetch_act_details_for_anaptyxi(inner_conn, result.act_upsert.act_id)
            for anaptyxi_client in anaptyxi_clients:
                await _attempt(
                    anaptyxi_client.program_period,
                    result.adam_normalized,
                    lambda anaptyxi_client=anaptyxi_client: resolve_funding_link_for_act(
                        inner_conn,
                        client=anaptyxi_client,
                        raw_store=raw_store,
                        act_id=result.act_upsert.act_id,
                        mis_candidates=result.act_upsert.funding_ref_candidates,
                        beneficiary_afm=act_details["buyer_afm"],
                        contractor_afm=result.act_upsert.contractor_afm_normalized,
                        act_title=act_details["title"],
                        act_date=act_details["date"],
                        related_ada_candidates=result.act_upsert.related_ada,
                        act_amount=act_details["amount"],
                        act_region=act_details["region"],
                    ),
                )

        if (
            mef_client is not None
            and result.act_upsert is not None
            and result.act_upsert.contractor_entity_id is not None
            and result.act_upsert.contractor_afm_normalized
            and result.act_upsert.contractor_afm_normalized not in attempted_mef_afms
        ):
            attempted_mef_afms.add(result.act_upsert.contractor_afm_normalized)
            await _attempt(
                "MEF",
                result.adam_normalized,
                lambda: resolve_expenses_for_contractor(
                    inner_conn,
                    client=mef_client,
                    raw_store=raw_store,
                    contractor_entity_id=result.act_upsert.contractor_entity_id,
                    afm_normalized=result.act_upsert.contractor_afm_normalized,
                ),
            )

        if opensearch_http_client is not None and opensearch_config is not None and result.act_upsert is not None:
            await _attempt(
                "OPENSEARCH",
                result.adam_normalized,
                lambda: index_single_act(
                    inner_conn,
                    opensearch_http_client,
                    opensearch_config,
                    result.act_upsert.act_id,
                ),
            )

    try:
        for resource in sorted(ALL_RESOURCES):
            partition_result = await ingest_khmdhs_partition(
                client=client,
                raw_store=raw_store,
                conn=conn,
                resource=resource,  # type: ignore[arg-type]
                date_from=date_from,
                date_to=date_to,
                on_ingest_result=_on_ingest_result,
                enrich_deduplicated=True,
            )
            totals["pages_fetched"] += partition_result.pages_fetched
            totals["records_fetched"] += partition_result.records_seen
            totals["records_upserted"] += partition_result.records_ingested
            totals["records_failed"] += partition_result.records_failed
    finally:
        await client.aclose()
        if diavgeia_client is not None:
            await diavgeia_client.aclose()
        if gemi_client is not None:
            await gemi_client.aclose()
        for anaptyxi_client in anaptyxi_clients:
            await anaptyxi_client.aclose()
        if mef_client is not None:
            await mef_client.aclose()
        if opensearch_http_client is not None:
            await opensearch_http_client.aclose()
        if alert_http_client is not None:
            await alert_http_client.aclose()

    return totals

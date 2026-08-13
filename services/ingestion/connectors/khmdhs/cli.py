"""Manual CLI entrypoint.

    python -m services.ingestion.connectors.khmdhs.cli backfill \\
        --date-from 2025-01-01 --date-to 2025-01-30 \\
        [--resource contract --resource payment ...] \\
        [--with-diavgeia] [--with-diavgeia-search] [--with-documents] [--with-gemi] [--with-anaptyxi] [--with-mef] [--with-opensearch]

Defaults to all five resources (request, notice, auction, contract,
payment) if none are given. Splits the requested range into <=30-day
windows (description.txt §16.1: never a full year at once, even if the API
would allow it).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from packages.domain.tables import (
    act_locations,
    act_parties,
    connector_runs,
    entities,
    entity_identifiers,
    procurement_acts,
)
from packages.source_clients.raw_store import configured_raw_store
from services.alerts.evaluate import (
    evaluate_and_fire_all_tenants,
    evaluate_company_status_change_for_all_tenants,
)
from services.alerts.factory import build_delivery_channel
from services.ingestion.connectors.anaptyxi.client import AnaptyxiClient
from services.ingestion.connectors.anaptyxi.config import (
    DEFAULT_PROGRAM_PERIOD as ANAPTYXI_DEFAULT_PROGRAM_PERIOD,
)
from services.ingestion.connectors.anaptyxi.config import (
    SUPPORTED_PROGRAM_PERIODS as ANAPTYXI_SUPPORTED_PROGRAM_PERIODS,
)
from services.ingestion.connectors.anaptyxi.config import AnaptyxiConnectorConfig
from services.ingestion.connectors.anaptyxi.resolve import resolve_funding_link_for_act
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.resolve import resolve_decision_for_ada, resolve_decision_via_search
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
from .client import ALL_RESOURCES, KhmdhsClient, KhmdhsResource
from .config import KhmdhsConnectorConfig
from .db_writer import IngestResult
from .documents import process_khmdhs_attachment
from .pipeline import ingest_khmdhs_partition

MAX_WINDOW_DAYS = 30


def _windows(date_from: date, date_to: date, max_days: int = MAX_WINDOW_DAYS) -> Iterator[tuple[date, date]]:
    cursor = date_from
    while cursor <= date_to:
        window_end = min(cursor + timedelta(days=max_days - 1), date_to)
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _fetch_act_title_and_buyer_name(
    conn: AsyncConnection, act_id
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


async def _fetch_act_details_for_anaptyxi(conn: AsyncConnection, act_id):
    """Level 2/4 (§19.2) need the act's own title/date/amount/region and
    its buyer's ΑΦΜ — none of which `ActUpsertResult` carries directly
    (unlike `related_ada`, which it already exposes as a trigger list)."""
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


async def _run_backfill(
    resources: list[KhmdhsResource],
    date_from: date,
    date_to: date,
    database_url: str,
    raw_root: str,
    resolve_adam_chains: bool,
    fire_alerts: bool,
    with_diavgeia: bool,
    with_diavgeia_search: bool = False,
    with_documents: bool = False,
    with_gemi: bool = False,
    with_anaptyxi: bool = False,
    anaptyxi_period: str = ANAPTYXI_DEFAULT_PROGRAM_PERIOD,
    with_mef: bool = False,
    with_opensearch: bool = False,
    window_days: int = MAX_WINDOW_DAYS,
    max_pages_per_window: int | None = None,
    max_records_per_window: int | None = None,
    resume_key: str | None = None,
    continue_on_error: bool = False,
) -> dict[str, dict[str, int]]:
    if window_days <= 0 or window_days > MAX_WINDOW_DAYS:
        raise ValueError(f"window_days must be between 1 and {MAX_WINDOW_DAYS}")
    if with_diavgeia_search and not with_diavgeia:
        raise ValueError("--with-diavgeia-search requires --with-diavgeia")

    config = KhmdhsConnectorConfig.from_env()
    client = KhmdhsClient(config)
    raw_store = configured_raw_store(raw_root)
    alert_http_client = httpx.AsyncClient(timeout=10.0)
    delivery_channel = build_delivery_channel(alert_http_client)
    engine = create_async_engine(_to_asyncpg_url(database_url))
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"pages": 0, "seen": 0, "ingested": 0, "failed": 0})

    opensearch_config = None
    opensearch_http_client = None
    if with_opensearch:
        opensearch_config = OpenSearchConfig.from_env()
        opensearch_http_client = httpx.AsyncClient(timeout=10.0)

    diavgeia_client = None
    if with_diavgeia:
        diavgeia_client = DiavgeiaClient(DiavgeiaConnectorConfig.from_env())

    gemi_client = None
    gemi_provider = None
    if with_gemi:
        gemi_client = GemiClient(GemiConnectorConfig.from_env())
        gemi_provider = GemiCompanyRegistryProvider(gemi_client)

    anaptyxi_client = None
    if with_anaptyxi:
        anaptyxi_client = AnaptyxiClient(AnaptyxiConnectorConfig.from_env(program_period=anaptyxi_period))

    mef_client = None
    if with_mef:
        mef_client = MefClient(MefConnectorConfig.from_env())

    async def _on_ingest_result(conn: AsyncConnection, resource: str, result: IngestResult) -> None:
        if with_documents and result.act_upsert is not None:
            attachment = await process_khmdhs_attachment(
                conn,
                resource=resource,
                adam=result.adam_normalized,
                act_id=result.act_upsert.act_id,
                rate_limiter=client.request_rate_limiter,
            )
            if attachment is not None:
                print(
                    f"  processed ΚΗΜΔΗΣ document {attachment.document_id} "
                    f"({attachment.page_count} page(s))"
                )
        if resolve_adam_chains:
            await resolve_adam_chain_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                seed_adam_normalized=result.adam_normalized,
                delivery_channel=delivery_channel if fire_alerts else None,
            )
        if fire_alerts and result.act_upsert is not None:
            fired = await evaluate_and_fire_all_tenants(
                conn, act_upsert=result.act_upsert, delivery_channel=delivery_channel
            )
            if fired:
                print(f"  fired {fired} alert(s) for {result.adam_normalized}")
        if diavgeia_client is not None and result.act_upsert is not None:
            any_direct_link = False
            for ada in result.act_upsert.related_ada:
                decision_act_id = await resolve_decision_for_ada(
                    conn,
                    client=diavgeia_client,
                    raw_store=raw_store,
                    ada=ada,
                    origin_act_id=result.act_upsert.act_id,
                    process_documents=with_documents,
                )
                if decision_act_id is not None:
                    any_direct_link = True
                    print(f"  linked Διαύγεια decision {ada} -> act {result.act_upsert.act_id}")
            if with_diavgeia_search and not any_direct_link:
                title, buyer_name = await _fetch_act_title_and_buyer_name(conn, result.act_upsert.act_id)
                if title and buyer_name:
                    search_act_id = await resolve_decision_via_search(
                        conn,
                        client=diavgeia_client,
                        raw_store=raw_store,
                        origin_act_id=result.act_upsert.act_id,
                        organization_query=buyer_name,
                        title_query=title,
                        process_documents=with_documents,
                    )
                    if search_act_id is not None:
                        print(f"  linked Διαύγεια decision via search -> act {result.act_upsert.act_id}")
        contractor_entities = (
            result.act_upsert.contractor_entities
            if result.act_upsert is not None
            else []
        )
        if gemi_provider is not None:
            for contractor_entity_id, contractor_afm in contractor_entities:
                if not contractor_afm:
                    continue
                snapshot_result = await resolve_company_snapshot(
                    conn,
                    provider=gemi_provider,
                    raw_store=raw_store,
                    afm_normalized=contractor_afm,
                    entity_id=contractor_entity_id,
                )
                if snapshot_result.wrote_new_snapshot:
                    print(f"  refreshed ΓΕΜΗ snapshot for {contractor_afm}")
                    status_fired = await evaluate_company_status_change_for_all_tenants(
                        conn,
                        entity_id=contractor_entity_id,
                        old_status=snapshot_result.old_status,
                        new_status=snapshot_result.new_status,
                        delivery_channel=delivery_channel,
                    )
                    if status_fired:
                        print(
                            f"  fired {status_fired} company.status_changed alert(s) "
                            f"({snapshot_result.old_status} -> {snapshot_result.new_status})"
                        )
        if anaptyxi_client is not None and result.act_upsert is not None and (
            result.act_upsert.funding_ref_candidates
            or any(afm for _, afm in contractor_entities)
        ):
            act_details = await _fetch_act_details_for_anaptyxi(conn, result.act_upsert.act_id)
            contractor_afms = [afm for _, afm in contractor_entities if afm] or [None]
            for contractor_afm in contractor_afms:
                funding_project_id = await resolve_funding_link_for_act(
                    conn,
                    client=anaptyxi_client,
                    raw_store=raw_store,
                    act_id=result.act_upsert.act_id,
                    mis_candidates=result.act_upsert.funding_ref_candidates,
                    beneficiary_afm=act_details["buyer_afm"],
                    contractor_afm=contractor_afm,
                    act_title=act_details["title"],
                    act_date=act_details["date"],
                    related_ada_candidates=result.act_upsert.related_ada,
                    act_amount=act_details["amount"],
                    act_region=act_details["region"],
                )
                if funding_project_id is not None:
                    print(f"  linked ΑΝΑΠΤΥΞΗ funding project -> act {result.act_upsert.act_id}")
        if mef_client is not None:
            for contractor_entity_id, contractor_afm in contractor_entities:
                if not contractor_afm:
                    continue
                ingested = await resolve_expenses_for_contractor(
                    conn,
                    client=mef_client,
                    raw_store=raw_store,
                    contractor_entity_id=contractor_entity_id,
                    afm_normalized=contractor_afm,
                )
                if ingested:
                    print(f"  ingested {ingested} ΜΕΦ expense(s) for {contractor_afm}")
        if opensearch_http_client is not None and opensearch_config is not None and result.act_upsert is not None:
            try:
                await index_single_act(conn, opensearch_http_client, opensearch_config, result.act_upsert.act_id)
            except Exception:  # noqa: BLE001 — a stale search index is fine; a stopped backfill is not
                logging.getLogger("procintel.khmdhs.cli").exception(
                    "OpenSearch indexing failed for act %s", result.act_upsert.act_id
                )

    hook = (
        _on_ingest_result
        if (
            resolve_adam_chains
            or fire_alerts
            or with_diavgeia
            or with_diavgeia_search
            or with_documents
            or with_gemi
            or with_anaptyxi
            or with_mef
            or with_opensearch
        )
        else None
    )

    async def _mark_started(conn: AsyncConnection, resource: str, partition_label: str) -> uuid.UUID | None:
        if not resume_key:
            return None
        existing = (
            await conn.execute(
                select(connector_runs.c.id).where(
                    connector_runs.c.source_system == "KHMDHS",
                    connector_runs.c.resource_type == resource,
                    connector_runs.c.partition_key == partition_label,
                    connector_runs.c.run_type == "MONTH_BACKFILL",
                    connector_runs.c.status == "SUCCEEDED",
                )
            )
        ).first()
        if existing is not None:
            return existing.id

        run_id = uuid.uuid4()
        await conn.execute(
            connector_runs.insert().values(
                id=run_id,
                source_system="KHMDHS",
                resource_type=resource,
                partition_key=partition_label,
                run_type="MONTH_BACKFILL",
                status="RUNNING",
                triggered_by=resume_key,
            )
        )
        await conn.commit()
        return run_id

    async def _is_completed(conn: AsyncConnection, resource: str, partition_label: str) -> bool:
        if not resume_key:
            return False
        row = (
            await conn.execute(
                select(connector_runs.c.id).where(
                    connector_runs.c.source_system == "KHMDHS",
                    connector_runs.c.resource_type == resource,
                    connector_runs.c.partition_key == partition_label,
                    connector_runs.c.run_type == "MONTH_BACKFILL",
                    connector_runs.c.status == "SUCCEEDED",
                )
            )
        ).first()
        return row is not None

    async def _mark_finished(
        conn: AsyncConnection,
        *,
        run_id: uuid.UUID | None,
        status: str,
        pages: int = 0,
        seen: int = 0,
        ingested: int = 0,
        error: str | None = None,
    ) -> None:
        if run_id is None:
            return
        values = {
            "status": status,
            "finished_at": datetime.now(timezone.utc),
            "pages_fetched": pages,
            "records_fetched": seen,
            "records_upserted": ingested,
        }
        if error is not None:
            values["error"] = {"message": error}
        await conn.execute(connector_runs.update().where(connector_runs.c.id == run_id).values(**values))
        await conn.commit()

    try:
        async with engine.connect() as conn:
            for resource in resources:
                for window_from, window_to in _windows(date_from, date_to, max_days=window_days):
                    partition_label = (
                        f"{resume_key}:{window_from.isoformat()}:{window_to.isoformat()}"
                        if resume_key
                        else f"{window_from.isoformat()}:{window_to.isoformat()}"
                    )
                    if await _is_completed(conn, resource, partition_label):
                        print(f"[{resource}] partition {window_from} -> {window_to} skipped (resume)")
                        continue

                    print(f"[{resource}] partition {window_from} -> {window_to}")
                    run_id = await _mark_started(conn, resource, partition_label)
                    try:
                        result = await ingest_khmdhs_partition(
                            client=client,
                            raw_store=raw_store,
                            conn=conn,
                            resource=resource,
                            date_from=window_from,
                            date_to=window_to,
                            on_ingest_result=hook,
                            max_pages=max_pages_per_window,
                            max_records=max_records_per_window,
                        )
                    except Exception as exc:  # noqa: BLE001 — record the failed partition before surfacing/continuing
                        totals[resource]["failed"] += 1
                        # str(exc) is empty for several real-world exceptions
                        # (notably httpx timeout errors, e.g. `httpx.ConnectTimeout`,
                        # which retry.py's own retryable-error tuple already retries
                        # `max_retry_attempts` times before giving up and re-raising
                        # the original) — printing just str(exc) then shows a blank
                        # "FAILED:" with no way to tell what actually happened.
                        description = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                        if conn.in_transaction():
                            await conn.rollback()
                        await _mark_finished(conn, run_id=run_id, status="FAILED", error=description)
                        if not continue_on_error:
                            raise
                        print(f"  FAILED: {description}")
                        continue

                    totals[resource]["pages"] += result.pages_fetched
                    totals[resource]["seen"] += result.records_seen
                    totals[resource]["ingested"] += result.records_ingested
                    totals[resource]["failed"] += result.records_failed
                    partial_error = None
                    if result.records_failed:
                        samples = " | ".join(
                            f"{failure['stage']} {failure['adam']}: {failure['error']}"
                            for failure in result.failed_records[:5]
                        )
                        partial_error = f"{result.records_failed} record failure(s)"
                        if samples:
                            partial_error = f"{partial_error}; {samples}"
                    await _mark_finished(
                        conn,
                        run_id=run_id,
                        status="SUCCEEDED" if result.records_failed == 0 else "PARTIAL",
                        pages=result.pages_fetched,
                        seen=result.records_seen,
                        ingested=result.records_ingested,
                        error=partial_error,
                    )
                    print(
                        f"  pages={result.pages_fetched} "
                        f"seen={result.records_seen} "
                        f"ingested={result.records_ingested} "
                        f"failed={result.records_failed}"
                        f"{' page-budget' if result.reached_page_budget else ''}"
                        f"{' record-budget' if result.reached_record_budget else ''}"
                    )
                    # a bounded sample (see pipeline.py's _MAX_RECORDED_FAILURES) —
                    # enough to spot a systemic issue (one field shape breaking
                    # every record) without flooding the log on a bad day
                    for failure in result.failed_records[:10]:
                        print(f"    FAILED [{failure['stage']}] {failure['adam']}: {failure['error']}")
    finally:
        await client.aclose()
        if diavgeia_client is not None:
            await diavgeia_client.aclose()
        if gemi_client is not None:
            await gemi_client.aclose()
        if anaptyxi_client is not None:
            await anaptyxi_client.aclose()
        if mef_client is not None:
            await mef_client.aclose()
        if opensearch_http_client is not None:
            await opensearch_http_client.aclose()
        await alert_http_client.aclose()
        await engine.dispose()

    return dict(totals)


def main() -> None:
    parser = argparse.ArgumentParser(description="ΚΗΜΔΗΣ connector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("backfill")
    backfill.add_argument("--date-from", required=True, type=date.fromisoformat)
    backfill.add_argument("--date-to", required=True, type=date.fromisoformat)
    backfill.add_argument(
        "--resource",
        action="append",
        choices=sorted(ALL_RESOURCES),
        dest="resources",
        help="repeatable; defaults to all five resources",
    )
    backfill.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    backfill.add_argument("--raw-root", default="./raw", help="local raw-storage root")
    backfill.add_argument(
        "--window-days",
        type=int,
        default=MAX_WINDOW_DAYS,
        help=f"split the requested range into N-day windows; max {MAX_WINDOW_DAYS}",
    )
    backfill.add_argument(
        "--max-pages-per-window",
        type=int,
        default=None,
        help="stop each resource/window after this many pages; useful for smoke/budgeted runs",
    )
    backfill.add_argument(
        "--max-records-per-window",
        type=int,
        default=None,
        help="stop each resource/window after this many records; useful for smoke/budgeted runs",
    )
    backfill.add_argument(
        "--resume-key",
        default=None,
        help="record successful resource/windows in connector_runs and skip them on rerun",
    )
    backfill.add_argument(
        "--continue-on-error",
        action="store_true",
        help="record a failed resource/window and continue with the rest",
    )
    backfill.add_argument(
        "--no-adam-chain",
        action="store_true",
        help="skip adamChain resolution (§16.5) — faster, but no process grouping",
    )
    backfill.add_argument(
        "--no-alerts",
        action="store_true",
        help="skip alert evaluation (§32) on new/changed contract acts",
    )
    backfill.add_argument(
        "--with-diavgeia",
        action="store_true",
        help="resolve Διαύγεια decisions for every ΑΔΑ referenced (§17.1) — opt-in, "
        "requires DIAVGEIA_API_BASE_URL",
    )
    backfill.add_argument(
        "--with-diavgeia-search",
        action="store_true",
        help="fall back to Διαύγεια SEARCH (buyer name + title, §17.4 "
        "DIAVGEIA_SEARCH_MATCH, confidence < 1.0) when no ΑΔΑ resolved directly "
        "— requires --with-diavgeia too",
    )
    backfill.add_argument(
        "--with-documents",
        action="store_true",
        help="download/OCR each ΚΗΜΔΗΣ attachment and each linked Διαύγεια "
        "decision PDF; opt-in because document extraction is heavier than "
        "the API-response fetches",
    )
    backfill.add_argument(
        "--with-gemi",
        action="store_true",
        help="enrich contractor entities and public documents from ΓΕΜΗ (§18.1) — "
        "opt-in, requires GEMI_API_KEY",
    )
    backfill.add_argument(
        "--with-anaptyxi",
        action="store_true",
        help="resolve ΑΝΑΠΤΥΞΗ funding links (§19.2 levels 1-4) — opt-in, requires "
        "the base URL for --anaptyxi-period's programming period",
    )
    backfill.add_argument(
        "--anaptyxi-period",
        default=ANAPTYXI_DEFAULT_PROGRAM_PERIOD,
        choices=ANAPTYXI_SUPPORTED_PROGRAM_PERIODS,
        help="ΑΝΑΠΤΥΞΗ programming period to resolve against (§19.3 — each is a "
        "separate deployment/env var); defaults to the best-documented period",
    )
    backfill.add_argument(
        "--with-mef",
        action="store_true",
        help="look up ΜΕΦ expenses for every resolved contractor and attempt tiered "
        "confidence linkage to acts (§20.2) — opt-in, uses the public API by default",
    )
    backfill.add_argument(
        "--with-opensearch",
        action="store_true",
        help="incrementally index every upserted act into OpenSearch (§11/§29) — "
        "opt-in, requires OPENSEARCH_URL",
    )

    args = parser.parse_args()

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("--database-url or $DATABASE_URL is required")

    resources: list[KhmdhsResource] = args.resources or sorted(ALL_RESOURCES)

    asyncio.run(
        _run_backfill(
            resources,
            args.date_from,
            args.date_to,
            database_url,
            args.raw_root,
            resolve_adam_chains=not args.no_adam_chain,
            fire_alerts=not args.no_alerts,
            with_diavgeia=args.with_diavgeia,
            with_diavgeia_search=args.with_diavgeia_search,
            with_documents=args.with_documents,
            with_gemi=args.with_gemi,
            with_anaptyxi=args.with_anaptyxi,
            anaptyxi_period=args.anaptyxi_period,
            with_mef=args.with_mef,
            with_opensearch=args.with_opensearch,
            window_days=args.window_days,
            max_pages_per_window=args.max_pages_per_window,
            max_records_per_window=args.max_records_per_window,
            resume_key=args.resume_key,
            continue_on_error=args.continue_on_error,
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Reconcile enrichment providers against already imported KHMDHS acts.

The normal importer enriches records only when their content is new. This
bounded job covers the historical case: KHMDHS may have been loaded before
Diavgeia, GEMI, or MEF was enabled. Local records are linked first; network
lookups then use each provider client's token bucket, retry, and cache rules.

Examples:
    python scripts/enrich_existing.py --dry-run --date-from 2026-06-01 --date-to 2026-06-30
    python scripts/enrich_existing.py --provider diavgeia --provider mef --limit 500
    python scripts/enrich_existing.py --provider diavgeia --local-only --limit 5000 --offset 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.domain.tables import (  # noqa: E402
    act_parties,
    connector_runs,
    entity_identifiers,
    procurement_acts,
    source_records,
)
from packages.source_clients.raw_store import configured_raw_store  # noqa: E402
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient  # noqa: E402
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig  # noqa: E402
from services.ingestion.connectors.diavgeia.resolve import (  # noqa: E402
    link_existing_decision_for_ada,
    resolve_decision_for_ada,
)
from services.ingestion.connectors.gemi.client import GemiClient  # noqa: E402
from services.ingestion.connectors.gemi.config import GemiConnectorConfig  # noqa: E402
from services.ingestion.connectors.gemi.provider import GemiCompanyRegistryProvider  # noqa: E402
from services.ingestion.connectors.gemi.resolve import resolve_company_snapshot  # noqa: E402
from services.ingestion.connectors.khmdhs.normalize import normalize_khmdhs_record  # noqa: E402
from services.ingestion.connectors.mef.client import MefClient  # noqa: E402
from services.ingestion.connectors.mef.config import MefConnectorConfig  # noqa: E402
from services.ingestion.connectors.mef.resolve import (  # noqa: E402
    relink_existing_expenses_for_contractor,
    resolve_expenses_for_contractor,
)

SUPPORTED_PROVIDERS = ("diavgeia", "gemi", "mef")
KHMDHS_RESOURCES = ("request", "notice", "auction", "contract", "payment")


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def _payload_path(payload_uri: str) -> Path:
    path = Path(payload_uri)
    if path.is_absolute() or path.exists():
        return path
    return PROJECT_ROOT / path


async def _candidate_rows(conn: AsyncConnection, args: argparse.Namespace) -> list[Any]:
    act_date = func.coalesce(
        procurement_acts.c.publication_date,
        procurement_acts.c.submission_date,
        procurement_acts.c.decision_date,
    )
    query = (
        select(
            source_records.c.id.label("source_record_id"),
            source_records.c.resource_type,
            source_records.c.payload_uri,
            source_records.c.fetched_at,
            procurement_acts.c.id.label("act_id"),
        )
        .select_from(
            source_records.join(
                procurement_acts,
                procurement_acts.c.source_record_id == source_records.c.id,
            )
        )
        .where(
            source_records.c.source_system == "KHMDHS",
            source_records.c.resource_type.in_(KHMDHS_RESOURCES),
            source_records.c.is_latest.is_(True),
            procurement_acts.c.is_current.is_(True),
        )
        .order_by(source_records.c.fetched_at.desc(), source_records.c.id)
        .limit(args.limit)
        .offset(args.offset)
    )
    if args.date_from:
        query = query.where(act_date >= args.date_from)
    if args.date_to:
        query = query.where(act_date <= args.date_to)
    return list((await conn.execute(query)).all())


async def _contractor_identity(
    conn: AsyncConnection,
    *,
    act_id: uuid.UUID,
) -> tuple[uuid.UUID, str] | None:
    row = (
        await conn.execute(
            select(act_parties.c.entity_id, entity_identifiers.c.value_normalized)
            .select_from(
                act_parties.join(
                    entity_identifiers,
                    entity_identifiers.c.entity_id == act_parties.c.entity_id,
                )
            )
            .where(
                act_parties.c.act_id == act_id,
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
                entity_identifiers.c.scheme == "AFM",
                entity_identifiers.c.is_current.is_(True),
            )
            .limit(1)
        )
    ).first()
    if row is None or not row.value_normalized:
        return None
    return row.entity_id, row.value_normalized


async def _start_run(
    conn: AsyncConnection,
    *,
    args: argparse.Namespace,
    providers: list[str],
) -> uuid.UUID:
    run_id = uuid.uuid4()
    date_scope = f"{args.date_from or 'any'}:{args.date_to or 'any'}:offset={args.offset}"
    await conn.execute(
        connector_runs.insert().values(
            id=run_id,
            source_system="ENRICHMENT",
            resource_type="existing_acts",
            partition_key=f"{date_scope}:{run_id}",
            run_type="RECONCILIATION",
            status="RUNNING",
            triggered_by=",".join(providers),
        )
    )
    await conn.commit()
    return run_id


async def _finish_run(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    stats: Counter[str],
    errors: list[str],
) -> None:
    values: dict[str, Any] = {
        "status": "PARTIAL" if errors else "SUCCEEDED",
        "finished_at": datetime.now(timezone.utc),
        "records_fetched": stats["acts_seen"],
        "records_upserted": stats["links_resolved"] + stats["snapshots_written"] + stats["expenses_ingested"],
    }
    if errors:
        values["error"] = {"message": f"{len(errors)} enrichment error(s)", "samples": errors[:10]}
    await conn.execute(connector_runs.update().where(connector_runs.c.id == run_id).values(**values))
    await conn.commit()


async def _run(args: argparse.Namespace) -> None:
    providers = args.provider or ["diavgeia", "mef"]
    if "gemi" in providers and not os.environ.get("GEMI_API_KEY") and not args.local_only:
        raise SystemExit("GEMI_API_KEY is required for network GEMI enrichment")
    if args.diavgeia_rate:
        os.environ["DIAVGEIA_RATE_LIMIT_PER_MINUTE"] = str(args.diavgeia_rate)
    if args.gemi_rate:
        os.environ["GEMI_RATE_LIMIT_PER_MINUTE"] = str(args.gemi_rate)
    if args.mef_rate:
        os.environ["MEF_RATE_LIMIT_PER_MINUTE"] = str(args.mef_rate)

    engine = create_async_engine(_to_asyncpg_url(args.database_url))
    raw_store = configured_raw_store(args.raw_root)
    diavgeia_client = (
        DiavgeiaClient(DiavgeiaConnectorConfig.from_env())
        if "diavgeia" in providers and not args.local_only
        else None
    )
    gemi_client = (
        GemiClient(GemiConnectorConfig.from_env())
        if "gemi" in providers and not args.local_only
        else None
    )
    gemi_provider = GemiCompanyRegistryProvider(gemi_client) if gemi_client else None
    mef_client = MefClient(MefConnectorConfig.from_env()) if "mef" in providers and not args.local_only else None
    stats: Counter[str] = Counter()
    errors: list[str] = []
    seen_contractors: set[uuid.UUID] = set()
    unavailable_adas: set[str] = set()

    try:
        async with engine.connect() as conn:
            rows = await _candidate_rows(conn, args)
            print(f"candidate acts: {len(rows)} (offset={args.offset}, next_offset={args.offset + len(rows)})")
            if args.dry_run:
                return
            run_id = await _start_run(conn, args=args, providers=providers)

            for row in rows:
                stats["acts_seen"] += 1
                try:
                    raw = json.loads(_payload_path(row.payload_uri).read_text(encoding="utf-8"))
                    normalized = normalize_khmdhs_record(raw, resource=row.resource_type)
                except Exception as exc:  # noqa: BLE001 - one historical payload must not stop reconciliation
                    errors.append(f"payload {row.source_record_id}: {type(exc).__name__}: {exc}")
                    continue

                if "diavgeia" in providers:
                    for ada in normalized.related_ada:
                        try:
                            decision_id = await link_existing_decision_for_ada(
                                conn,
                                ada=ada,
                                origin_act_id=row.act_id,
                            )
                            if decision_id is not None:
                                stats["links_resolved"] += 1
                                stats["diavgeia_local"] += 1
                                continue
                            if diavgeia_client is None or ada in unavailable_adas:
                                continue
                            decision_id = await resolve_decision_for_ada(
                                conn,
                                client=diavgeia_client,
                                raw_store=raw_store,
                                ada=ada,
                                origin_act_id=row.act_id,
                            )
                            if decision_id is None:
                                unavailable_adas.add(ada)
                            else:
                                stats["links_resolved"] += 1
                                stats["diavgeia_fetched"] += 1
                        except Exception as exc:  # noqa: BLE001 - provider isolation is intentional
                            await conn.rollback()
                            errors.append(f"Diavgeia {ada}: {type(exc).__name__}: {exc}")

                contractor = await _contractor_identity(conn, act_id=row.act_id)
                if contractor is None or contractor[0] in seen_contractors:
                    continue
                contractor_entity_id, contractor_afm = contractor
                seen_contractors.add(contractor_entity_id)

                if "mef" in providers:
                    try:
                        stats["mef_local_links"] += await relink_existing_expenses_for_contractor(
                            conn,
                            contractor_entity_id=contractor_entity_id,
                            afm_normalized=contractor_afm,
                        )
                        if mef_client is not None:
                            stats["expenses_ingested"] += await resolve_expenses_for_contractor(
                                conn,
                                client=mef_client,
                                raw_store=raw_store,
                                contractor_entity_id=contractor_entity_id,
                                afm_normalized=contractor_afm,
                            )
                    except Exception as exc:  # noqa: BLE001 - provider isolation is intentional
                        await conn.rollback()
                        errors.append(f"MEF {contractor_afm}: {type(exc).__name__}: {exc}")

                if gemi_provider is not None:
                    try:
                        result = await resolve_company_snapshot(
                            conn,
                            provider=gemi_provider,
                            raw_store=raw_store,
                            afm_normalized=contractor_afm,
                            entity_id=contractor_entity_id,
                        )
                        if result.wrote_new_snapshot:
                            stats["snapshots_written"] += 1
                    except Exception as exc:  # noqa: BLE001 - provider isolation is intentional
                        await conn.rollback()
                        errors.append(f"GEMI {contractor_afm}: {type(exc).__name__}: {exc}")

            stats["links_resolved"] += stats["mef_local_links"]
            await _finish_run(conn, run_id=run_id, stats=stats, errors=errors)
            print(json.dumps({"stats": dict(stats), "errors": errors[:10]}, ensure_ascii=False, indent=2))
    finally:
        if diavgeia_client is not None:
            await diavgeia_client.aclose()
        if gemi_client is not None:
            await gemi_client.aclose()
        if mef_client is not None:
            await mef_client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))
    parser.add_argument("--provider", action="append", choices=SUPPORTED_PROVIDERS)
    parser.add_argument("--date-from", type=date.fromisoformat)
    parser.add_argument("--date-to", type=date.fromisoformat)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--local-only", action="store_true", help="write local links without provider HTTP calls")
    parser.add_argument("--dry-run", action="store_true", help="show candidate count without writing or calling providers")
    parser.add_argument("--diavgeia-rate", type=float)
    parser.add_argument("--gemi-rate", type=float)
    parser.add_argument("--mef-rate", type=float)
    args = parser.parse_args()

    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if (args.date_from is None) != (args.date_to is None):
        parser.error("--date-from and --date-to must be passed together")
    if args.limit <= 0 or args.limit > 5000:
        parser.error("--limit must be between 1 and 5000")
    if args.offset < 0:
        parser.error("--offset must be zero or greater")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

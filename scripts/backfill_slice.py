#!/usr/bin/env python3
"""Run and audit a resumable procurement vertical slice of at most two days.

The command is intentionally conservative:

* it refuses a database whose name does not contain ``slice`` unless the
  operator explicitly overrides that guard;
* primary records, provider enrichments and downstream analytics have
  independent failure boundaries;
* every missing credential or unavailable upstream remains visible in the
  generated JSON report instead of being treated as a successful empty result.

Example:

    DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel_slice \
    python scripts/backfill_slice.py \
      --date-from 2026-07-28 \
      --date-to 2026-07-29 \
      --report-path artifacts/slice-2026-07-28_2026-07-29.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.domain.tables import tenants  # noqa: E402
from services.analytics.opportunity_scoring import (  # noqa: E402
    score_opportunities_for_tenant,
)
from services.analytics.refresh import refresh_all_marts  # noqa: E402
from services.competitors.participation import (  # noqa: E402
    backfill_winner_participations,
)
from services.data_quality.service import run_data_quality_checks  # noqa: E402
from services.geospatial.config import GeocoderConfig  # noqa: E402
from services.geospatial.service import run_pending_jobs  # noqa: E402
from services.ingestion.connectors.anaptyxi.config import (  # noqa: E402
    SUPPORTED_PROGRAM_PERIODS,
    AnaptyxiConnectorConfig,
)
from services.ingestion.connectors.ckan.scheduled import (  # noqa: E402
    onboard_default_ckan_datasets,
    refresh_due_ckan_datasets,
)
from services.ingestion.connectors.diavgeia.config import (  # noqa: E402
    DiavgeiaConnectorConfig,
)
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient  # noqa: E402
from services.ingestion.connectors.diavgeia.resolve import (  # noqa: E402
    backfill_decision_references,
)
from services.ingestion.connectors.gemi.config import GemiConnectorConfig  # noqa: E402
from services.ingestion.connectors.inspire.scheduled import (  # noqa: E402
    refresh_inspire_reference_sources,
)
from services.ingestion.connectors.khmdhs.scheduled import (  # noqa: E402
    run_scheduled_window as run_khmdhs_window,
)
from services.ingestion.connectors.mef.config import MefConnectorConfig  # noqa: E402
from services.ingestion.connectors.ted.scheduled import (  # noqa: E402
    run_scheduled_window as run_ted_window,
)
from services.ingestion.connectors.vies.config import ViesConnectorConfig  # noqa: E402
from services.ingestion.enrichment_reconciliation import (  # noqa: E402
    enqueue_process_diavgeia_search_jobs,
)
from services.ingestion.enrichment_worker import (  # noqa: E402
    run_pending_enrichment_jobs,
)
from packages.source_clients.raw_store import LocalFilesystemRawStore  # noqa: E402
from services.search_index.config import OpenSearchConfig  # noqa: E402
from services.search_index.indexer import reindex_all_acts  # noqa: E402


@dataclass
class Stage:
    status: str
    result: Any = None
    error: str | None = None


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _optional(factory: Callable[[], Any]) -> Any | None:
    try:
        return factory()
    except RuntimeError:
        return None


def _database_name(database_url: str) -> str:
    return urlparse(database_url.replace("+asyncpg", "")).path.rsplit("/", 1)[-1]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, uuid.UUID)):
        return str(value)
    return value


async def _stage(
    stages: dict[str, Stage],
    name: str,
    operation: Callable[[], Awaitable[Any]],
) -> Any | None:
    print(f"[{name}] started", flush=True)
    try:
        result = await operation()
    except Exception as exc:  # noqa: BLE001 - report every stage independently
        description = f"{type(exc).__name__}: {exc}"
        stages[name] = Stage(status="FAILED", error=description)
        print(f"[{name}] FAILED: {description}", flush=True)
        return None
    stages[name] = Stage(status="SUCCEEDED", result=_jsonable(result))
    print(f"[{name}] succeeded", flush=True)
    return result


async def _coverage(conn: AsyncConnection) -> dict[str, Any]:
    source_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT source_system, resource_type, parse_status, COUNT(*) AS count
                FROM source_records
                GROUP BY source_system, resource_type, parse_status
                ORDER BY source_system, resource_type, parse_status
                """
            )
        )
    ).mappings().all()
    queue_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT provider, status,
                       COALESCE((last_error->>'permanent')::boolean, FALSE)
                         AS permanent,
                       COUNT(*) AS count
                FROM enrichment_jobs
                GROUP BY provider, status,
                         COALESCE((last_error->>'permanent')::boolean, FALSE)
                ORDER BY provider, status, permanent
                """
            )
        )
    ).mappings().all()
    quality_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT issue_code, severity, status, COUNT(*) AS count
                FROM data_quality_issues
                GROUP BY issue_code, severity, status
                ORDER BY severity, issue_code, status
                """
            )
        )
    ).mappings().all()
    totals = (
        await conn.execute(
            sa.text(
                """
                SELECT
                  (SELECT COUNT(*) FROM source_records) AS source_records,
                  (SELECT COUNT(*) FROM procurement_acts WHERE is_current) AS acts,
                  (SELECT COUNT(*) FROM procurement_processes) AS processes,
                  (SELECT COUNT(DISTINCT act_id) FROM act_cpv_codes) AS acts_with_cpv,
                  (SELECT COUNT(DISTINCT act_id) FROM act_parties
                     WHERE party_role IN ('SUPPLIER', 'CONTRACTOR')) AS acts_with_supplier,
                  (SELECT COUNT(DISTINCT act_id) FROM documents
                     WHERE act_id IS NOT NULL) AS acts_with_documents,
                  (SELECT COUNT(DISTINCT act_id) FROM act_locations) AS acts_with_location,
                  (SELECT COUNT(DISTINCT act_id) FROM act_locations
                     WHERE geom IS NOT NULL) AS acts_with_precise_location,
                  (SELECT COUNT(*) FROM entity_company_snapshots) AS company_snapshots,
                  (SELECT COUNT(*) FROM funding_projects) AS funding_projects,
                  (SELECT COUNT(*) FROM funding_links) AS funding_links,
                  (SELECT COUNT(*) FROM mef_expenses) AS mef_expenses,
                  (SELECT COUNT(*) FROM ted_notice_details) AS ted_notices,
                  (SELECT COUNT(*) FROM nuts_areas
                     WHERE classification_version='NUTS-2024') AS nuts_areas,
                  (SELECT COUNT(DISTINCT postal_code) FROM postal_code_nuts
                     WHERE country_code='GR') AS postal_codes,
                  (SELECT COUNT(*) FROM administrative_boundaries
                     WHERE boundary_type='REGION') AS regions,
                  (SELECT COUNT(*) FROM administrative_boundaries
                     WHERE boundary_type='REGIONAL_UNIT') AS regional_units,
                  (SELECT COUNT(*) FROM administrative_boundaries
                     WHERE boundary_type='MUNICIPALITY') AS municipalities,
                  (SELECT COUNT(*) FROM process_participations) AS participations,
                  (SELECT COUNT(*) FROM field_provenance) AS provenance_fields,
                  (SELECT COUNT(*) FROM document_pages) AS document_pages,
                  (SELECT COUNT(*) FROM document_compliance_fields) AS compliance_fields
                """
            )
        )
    ).mappings().one()
    links = (
        await conn.execute(
            sa.text(
                """
                SELECT link_type, COUNT(*) AS count
                FROM act_links
                GROUP BY link_type
                ORDER BY link_type
                """
            )
        )
    ).mappings().all()
    capabilities = (
        await conn.execute(
            sa.text(
                """
                SELECT catalog_source, service_type, status, http_status,
                       checked_at, last_error
                FROM spatial_service_capabilities
                ORDER BY catalog_source, service_type
                """
            )
        )
    ).mappings().all()
    return {
        "totals": dict(totals),
        "source_records": [dict(row) for row in source_rows],
        "enrichment_jobs": [dict(row) for row in queue_rows],
        "data_quality": [dict(row) for row in quality_rows],
        "act_links": [dict(row) for row in links],
        "spatial_capabilities": [dict(row) for row in capabilities],
    }


def _provider_status() -> dict[str, dict[str, Any]]:
    return {
        "KHMDHS": {"configured": True},
        "DIAVGEIA": {"configured": True},
        "TED": {"configured": True},
        "MEF": {"configured": True},
        "GEMI": {
            "configured": bool(os.environ.get("GEMI_API_KEY")),
            "missing": None
            if os.environ.get("GEMI_API_KEY")
            else "GEMI_API_KEY",
        },
        "ANAPTYXI_2007_2013": {
            "configured": True,
            "public_default": "https://2013.anaptyxi.gov.gr",
        },
        "ANAPTYXI_2014_2020": {
            "configured": True,
            "public_default": "https://anaptyxi.gov.gr",
        },
        "ANAPTYXI_2021_2027": {
            "configured": bool(
                os.environ.get("ANAPTYXI_2021_2027_API_BASE_URL")
            ),
            "blocked_upstream": not bool(
                os.environ.get("ANAPTYXI_2021_2027_API_BASE_URL")
            ),
            "missing": (
                None
                if os.environ.get("ANAPTYXI_2021_2027_API_BASE_URL")
                else "validated project-level API"
            ),
        },
        "OPENSEARCH": {
            "configured": bool(os.environ.get("OPENSEARCH_URL")),
            "missing": None
            if os.environ.get("OPENSEARCH_URL")
            else "OPENSEARCH_URL",
        },
    }


def _build_verdict(
    coverage: dict[str, Any],
    provider_status: dict[str, dict[str, Any]],
    stages: dict[str, Stage],
) -> dict[str, Any]:
    failed_stages = [
        name for name, stage in stages.items() if stage.status == "FAILED"
    ]
    blocked_config = [
        name
        for name, state in provider_status.items()
        if not state["configured"] and not state.get("blocked_upstream")
    ]
    blocked_upstream = [
        row["catalog_source"]
        for row in coverage["spatial_capabilities"]
        if row["status"] == "BLOCKED_UPSTREAM"
    ]
    blocked_upstream.extend(
        name
        for name, state in provider_status.items()
        if state.get("blocked_upstream")
    )
    core_failures = sum(
        int(row["count"])
        for row in coverage["source_records"]
        if row["parse_status"] in {"FAILED", "QUARANTINED"}
        and row["source_system"] in {"KHMDHS", "TED"}
    )
    dead_enrichments = sum(
        int(row["count"])
        for row in coverage["enrichment_jobs"]
        if row["status"] == "DEAD" and not row.get("permanent")
    )
    quarantined_enrichments = sum(
        int(row["count"])
        for row in coverage["enrichment_jobs"]
        if row["status"] == "DEAD" and row.get("permanent")
    )
    deferred_enrichments = sum(
        int(row["count"])
        for row in coverage["enrichment_jobs"]
        if row["status"] in {"QUEUED", "FAILED", "RUNNING"}
    )
    open_quality_errors = sum(
        int(row["count"])
        for row in coverage["data_quality"]
        if row["severity"] == "ERROR" and row["status"] == "OPEN"
    )
    if failed_stages or core_failures or dead_enrichments:
        status = "FAILED"
    elif deferred_enrichments or open_quality_errors:
        status = (
            "PARTIAL_WITH_EXTERNAL_BLOCKERS"
            if blocked_config or blocked_upstream
            else "PARTIAL"
        )
    elif blocked_config or blocked_upstream:
        status = "COMPLETE_WITH_EXTERNAL_BLOCKERS"
    else:
        status = "COMPLETE"
    return {
        "status": status,
        "failed_stages": failed_stages,
        "core_parse_failures": core_failures,
        "dead_enrichments": dead_enrichments,
        "quarantined_enrichments": quarantined_enrichments,
        "deferred_enrichments": deferred_enrichments,
        "open_quality_errors": open_quality_errors,
        "blocked_config": blocked_config,
        "blocked_upstream": blocked_upstream,
    }


async def _snapshot(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    engine = create_async_engine(_async_url(args.database_url))
    try:
        async with engine.connect() as conn:
            coverage = await _coverage(conn)
    finally:
        await engine.dispose()
    provider_status = _provider_status()
    verdict = _build_verdict(coverage, provider_status, {})
    report = {
        "slice": {
            "date_from": str(args.date_from),
            "date_to": str(args.date_to),
            "database": _database_name(args.database_url),
        },
        "provider_configuration": provider_status,
        "stages": {},
        "coverage": coverage,
        "verdict": verdict,
    }
    return report, 1 if verdict["status"] == "FAILED" else 0 if verdict["status"] == "COMPLETE" else 2


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    stages: dict[str, Stage] = {}
    engine = create_async_engine(_async_url(args.database_url))
    provider_status = _provider_status()
    opensearch_config = _optional(OpenSearchConfig.from_env)
    diavgeia_config = _optional(DiavgeiaConnectorConfig.from_env)
    gemi_config = _optional(GemiConnectorConfig.from_env)
    mef_config = _optional(MefConnectorConfig.from_env)
    vies_config = _optional(ViesConnectorConfig.from_env)
    anaptyxi_configs = tuple(
        config
        for period in SUPPORTED_PROGRAM_PERIODS
        if (
            config := _optional(
                lambda period=period: AnaptyxiConnectorConfig.from_env(
                    program_period=period
                )
            )
        )
        is not None
    )

    try:
        async with engine.connect() as conn:
            async def run_stage(
                name: str,
                operation: Callable[[], Awaitable[Any]],
            ) -> Any | None:
                result = await _stage(stages, name, operation)
                if stages[name].status == "FAILED":
                    await conn.rollback()
                return result

            await run_stage(
                "khmdhs",
                lambda: run_khmdhs_window(
                    conn,
                    args.date_from,
                    args.date_to,
                    raw_root=args.raw_root,
                    # The slice performs one deterministic full reindex after
                    # enrichments, so avoid per-record indexing and queue work.
                    opensearch_config=None,
                    diavgeia_config=diavgeia_config,
                    diavgeia_search=False,
                    gemi_config=gemi_config,
                    anaptyxi_configs=anaptyxi_configs,
                    mef_config=mef_config,
                    process_documents=True,
                    inline_enrichment_providers={"ALERTS"},
                    queue_unconfigured_providers=True,
                    max_pages_per_resource=args.max_pages_per_resource,
                    max_records_per_resource=args.max_records_per_resource,
                ),
            )
            await run_stage(
                "ted",
                lambda: run_ted_window(
                    conn,
                    args.date_from,
                    args.date_to,
                    country="GR",
                    raw_root=args.raw_root,
                    vies_config=vies_config,
                    vies_lookup_budget=args.vies_budget,
                    opensearch_config=None,
                ),
            )
            await run_stage(
                "adamchain_enrichment",
                lambda: run_pending_enrichment_jobs(
                    conn,
                    raw_root=args.raw_root,
                    limit=max(args.enrichment_limit, args.adamchain_budget * 10),
                    providers={"KHMDHS_ADAMCHAIN"},
                    provider_budgets={
                        "KHMDHS_ADAMCHAIN": args.adamchain_budget,
                    },
                ),
            )
            await run_stage(
                "diavgeia_search_reconciliation",
                lambda: enqueue_process_diavgeia_search_jobs(conn),
            )
            await run_stage(
                "provider_enrichment",
                lambda: run_pending_enrichment_jobs(
                    conn,
                    raw_root=args.raw_root,
                    limit=args.enrichment_limit,
                    providers={
                        "KHMDHS_DOCUMENT",
                        "DIAVGEIA",
                        "DIAVGEIA_SEARCH",
                        "GEMI",
                        "MEF",
                        *SUPPORTED_PROGRAM_PERIODS,
                    },
                    provider_budgets={
                        "KHMDHS_DOCUMENT": args.document_budget,
                        "DIAVGEIA": args.diavgeia_budget,
                        "DIAVGEIA_SEARCH": args.diavgeia_budget,
                        "GEMI": args.gemi_budget,
                        "MEF": args.mef_budget,
                        **{
                            period: args.anaptyxi_budget
                            for period in SUPPORTED_PROGRAM_PERIODS
                        },
                    },
                ),
            )
            if diavgeia_config is not None:
                async def _diavgeia_references() -> dict[str, int]:
                    client = DiavgeiaClient(diavgeia_config)
                    try:
                        return await backfill_decision_references(
                            conn,
                            client=client,
                            raw_store=LocalFilesystemRawStore(args.raw_root),
                            limit=args.diavgeia_reference_budget,
                            process_documents=True,
                        )
                    finally:
                        await client.aclose()

                await run_stage(
                    "diavgeia_references",
                    _diavgeia_references,
                )
            await run_stage(
                "inspire_references",
                lambda: refresh_inspire_reference_sources(
                    conn,
                    raw_root=args.raw_root,
                ),
            )

            if not args.no_ckan:
                async def _ckan() -> dict[str, Any]:
                    onboarded = await onboard_default_ckan_datasets(
                        conn,
                        database_url=args.database_url,
                        raw_root=args.raw_root,
                    )
                    refreshed = await refresh_due_ckan_datasets(
                        conn,
                        database_url=args.database_url,
                        raw_root=args.raw_root,
                    )
                    return {
                        "onboarded": onboarded,
                        "refreshed": [_jsonable(item) for item in refreshed],
                    }

                await run_stage("ckan_references", _ckan)

            await run_stage(
                "competition",
                lambda: backfill_winner_participations(conn),
            )

            async def _geospatial() -> dict[str, int]:
                counts: dict[str, int] = {}
                remaining = args.geospatial_limit
                config = _optional(GeocoderConfig.from_env)
                while remaining > 0:
                    outcomes = await run_pending_jobs(
                        conn,
                        batch_size=min(args.geospatial_batch_size, remaining),
                        geocoder_config=config,
                    )
                    if not outcomes:
                        break
                    remaining -= len(outcomes)
                    for outcome in outcomes:
                        counts[outcome.status] = counts.get(outcome.status, 0) + 1
                counts["processed"] = args.geospatial_limit - remaining
                return counts

            await run_stage("geospatial", _geospatial)
            await run_stage(
                "data_quality",
                lambda: run_data_quality_checks(
                    conn,
                    date_from=args.date_from,
                    date_to=args.date_to,
                ),
            )
            await run_stage("analytics_marts", lambda: refresh_all_marts(conn))

            async def _score() -> dict[str, int]:
                tenant_ids = (
                    await conn.execute(sa.select(tenants.c.id))
                ).scalars().all()
                written = 0
                for tenant_id in tenant_ids:
                    result = await score_opportunities_for_tenant(
                        conn,
                        tenant_id=tenant_id,
                        lookback_days=args.scoring_lookback_days,
                    )
                    written += result.scores_written
                return {"tenants": len(tenant_ids), "scores_written": written}

            await run_stage("opportunity_scoring", _score)

            if opensearch_config is not None:
                async def _search() -> Any:
                    async with httpx.AsyncClient(
                        timeout=opensearch_config.request_timeout_seconds
                    ) as client:
                        return await reindex_all_acts(
                            conn,
                            client,
                            opensearch_config,
                        )

                await run_stage("search_reindex", _search)

            coverage = await _coverage(conn)
    finally:
        await engine.dispose()

    verdict = _build_verdict(coverage, provider_status, stages)

    report = {
        "slice": {
            "date_from": str(args.date_from),
            "date_to": str(args.date_to),
            "database": _database_name(args.database_url),
        },
        "provider_configuration": provider_status,
        "stages": {
            name: _jsonable(stage)
            for name, stage in stages.items()
        },
        "coverage": coverage,
        "verdict": verdict,
    }
    exit_code = 1 if verdict["status"] == "FAILED" else 0 if verdict["status"] == "COMPLETE" else 2
    return report, exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    parser.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--allow-non-isolated-database", action="store_true")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="read current coverage without running ingestion or enrichments",
    )
    parser.add_argument("--no-ckan", action="store_true")
    parser.add_argument("--max-pages-per-resource", type=int)
    parser.add_argument("--max-records-per-resource", type=int)
    parser.add_argument("--enrichment-limit", type=int, default=3000)
    parser.add_argument("--adamchain-budget", type=int, default=1000)
    parser.add_argument("--document-budget", type=int, default=100)
    parser.add_argument("--diavgeia-budget", type=int, default=1000)
    parser.add_argument("--diavgeia-reference-budget", type=int, default=1000)
    parser.add_argument("--gemi-budget", type=int, default=500)
    parser.add_argument("--mef-budget", type=int, default=500)
    parser.add_argument("--anaptyxi-budget", type=int, default=500)
    parser.add_argument("--vies-budget", type=int, default=500)
    parser.add_argument("--geospatial-limit", type=int, default=5000)
    parser.add_argument("--geospatial-batch-size", type=int, default=250)
    parser.add_argument("--scoring-lookback-days", type=int, default=120)
    args = parser.parse_args()

    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.date_to < args.date_from:
        parser.error("--date-to must be on or after --date-from")
    if (args.date_to - args.date_from).days > 1:
        parser.error("this command accepts at most two calendar days")
    database_name = _database_name(args.database_url)
    if "slice" not in database_name.casefold() and not args.allow_non_isolated_database:
        parser.error(
            f"refusing non-isolated database {database_name!r}; "
            "use a database containing 'slice' or pass "
            "--allow-non-isolated-database explicitly"
        )
    numeric_values = (
        args.enrichment_limit,
        args.adamchain_budget,
        args.document_budget,
        args.diavgeia_budget,
        args.diavgeia_reference_budget,
        args.gemi_budget,
        args.mef_budget,
        args.anaptyxi_budget,
        args.vies_budget,
        args.geospatial_limit,
        args.geospatial_batch_size,
    )
    if any(value < 0 for value in numeric_values):
        parser.error("budgets and limits must be non-negative")

    report, exit_code = asyncio.run(
        _snapshot(args) if args.report_only else _run(args)
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"coverage report: {args.report_path}")
    print(rendered)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

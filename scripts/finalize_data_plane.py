#!/usr/bin/env python3
"""Finalize a rebuilt database and emit a launch-readiness coverage report."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.analytics.opportunity_scoring import (  # noqa: E402
    score_opportunities_for_tenant,
    tenant_ids_with_business_profiles,
)
from services.analytics.refresh import refresh_all_marts  # noqa: E402
from services.competitors.participation import (  # noqa: E402
    backfill_winner_participations,
)
from services.data_quality.service import run_data_quality_checks  # noqa: E402
from services.geospatial.config import GeocoderConfig  # noqa: E402
from services.geospatial.service import run_pending_jobs  # noqa: E402
from services.search_index.config import OpenSearchConfig  # noqa: E402
from services.search_index.indexer import (  # noqa: E402
    rebuild_all_indexes_atomic,
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


async def _stage(
    conn: AsyncConnection,
    stages: dict[str, dict[str, Any]],
    name: str,
    operation: Callable[[], Awaitable[Any]],
) -> Any | None:
    print(f"[{name}] started", flush=True)
    try:
        result = await operation()
    except Exception as exc:  # noqa: BLE001 - finalization stages are isolated
        await conn.rollback()
        stages[name] = {
            "status": "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(f"[{name}] FAILED: {type(exc).__name__}: {exc}", flush=True)
        return None
    stages[name] = {"status": "SUCCEEDED", "result": _jsonable(result)}
    print(f"[{name}] succeeded", flush=True)
    return result


async def _coverage(conn: AsyncConnection) -> dict[str, Any]:
    facts = (
        await conn.execute(
            sa.text(
                """
                WITH current_acts AS (
                    SELECT a.*,
                           NOT (
                               source.source_system = 'KHMDHS'
                               AND source.resource_type = 'adamChain'
                               AND a.title IS NULL
                               AND a.amount_net IS NULL
                               AND a.amount_gross IS NULL
                               AND a.publication_date IS NULL
                               AND a.submission_date IS NULL
                               AND a.decision_date IS NULL
                               AND a.start_date IS NULL
                               AND a.end_date IS NULL
                           )
                           AND NOT EXISTS (
                               SELECT 1
                               FROM data_quality_issues issue
                               WHERE issue.object_id = a.id
                                 AND LOWER(COALESCE(issue.object_type, '')) IN (
                                     'procurement_act', 'procurement_acts'
                                 )
                                 AND issue.severity IN ('ERROR', 'BLOCKING')
                                 AND issue.status <> 'RESOLVED'
                           ) AS eligible
                    FROM procurement_acts a
                    JOIN source_records source ON source.id = a.source_record_id
                    WHERE a.is_current = TRUE
                )
                SELECT
                    COUNT(*) AS current_acts,
                    COUNT(*) FILTER (WHERE eligible) AS eligible_acts,
                    COUNT(*) FILTER (WHERE NOT eligible) AS quarantined_acts,
                    COUNT(*) FILTER (
                        WHERE eligible AND EXISTS (
                            SELECT 1 FROM act_cpv_codes cpv WHERE cpv.act_id=a.id
                        )
                    ) AS with_cpv,
                    COUNT(*) FILTER (
                        WHERE eligible AND EXISTS (
                            SELECT 1 FROM act_parties party
                            WHERE party.act_id=a.id
                              AND party.party_role IN ('SUPPLIER','CONTRACTOR')
                        )
                    ) AS with_supplier,
                    COUNT(*) FILTER (
                        WHERE eligible AND EXISTS (
                            SELECT 1 FROM documents document WHERE document.act_id=a.id
                        )
                    ) AS with_documents,
                    COUNT(*) FILTER (
                        WHERE eligible AND EXISTS (
                            SELECT 1 FROM act_locations location
                            WHERE location.act_id=a.id AND location.geom IS NOT NULL
                        )
                    ) AS with_precise_geography,
                    COUNT(*) FILTER (
                        WHERE eligible AND a.title IS NOT NULL
                    ) AS with_title,
                    COUNT(*) FILTER (
                        WHERE eligible AND COALESCE(
                            a.publication_date, a.submission_date,
                            a.decision_date, a.start_date, a.end_date
                        ) IS NOT NULL
                    ) AS with_event_date,
                    COUNT(*) FILTER (
                        WHERE eligible AND COALESCE(a.amount_gross,a.amount_net) IS NOT NULL
                    ) AS with_amount
                FROM current_acts a
                """
            )
        )
    ).mappings().one()
    quality = (
        await conn.execute(
            sa.text(
                """
                SELECT issue_code, severity, COUNT(*) AS count
                FROM data_quality_issues
                WHERE status IN ('OPEN','ACKNOWLEDGED')
                GROUP BY issue_code, severity
                ORDER BY severity, issue_code
                """
            )
        )
    ).mappings().all()
    quality_gate = (
        await conn.execute(
            sa.text(
                """
                WITH open_errors AS (
                    SELECT issue.*,
                           CASE
                               WHEN LOWER(COALESCE(issue.object_type, '')) IN (
                                   'procurement_act', 'procurement_acts'
                               ) THEN procintel_act_is_analytics_eligible(issue.object_id)
                               WHEN UPPER(COALESCE(issue.object_type, '')) = 'ENTITY'
                                    AND issue.issue_code = 'INVALID_AFM_CHECKSUM'
                               THEN NOT EXISTS (
                                   SELECT 1
                                   FROM entity_identifiers identifier
                                   WHERE identifier.entity_id = issue.object_id
                                     AND identifier.scheme = 'AFM'
                                     AND identifier.is_current = TRUE
                                     AND identifier.identifier_valid = FALSE
                                     AND identifier.match_eligibility = 'RESTRICTED'
                               )
                               ELSE TRUE
                           END AS leaks_from_quarantine
                    FROM data_quality_issues issue
                    WHERE issue.status IN ('OPEN', 'ACKNOWLEDGED')
                      AND issue.severity IN ('ERROR', 'BLOCKING')
                      AND issue.issue_code <> 'INCOMPLETE_ADAMCHAIN_PLACEHOLDER'
                )
                SELECT
                    COUNT(*) AS open_errors,
                    COUNT(*) FILTER (WHERE NOT leaks_from_quarantine)
                        AS quarantined_errors,
                    COUNT(*) FILTER (WHERE leaks_from_quarantine)
                        AS unquarantined_errors
                FROM open_errors
                """
            )
        )
    ).mappings().one()
    enrichments = (
        await conn.execute(
            sa.text(
                """
                SELECT provider, status, COUNT(*) AS count
                FROM enrichment_jobs
                GROUP BY provider, status
                ORDER BY provider, status
                """
            )
        )
    ).mappings().all()
    geospatial = (
        await conn.execute(
            sa.text(
                """
                SELECT status, COUNT(*) AS count
                FROM geospatial_enrichment_jobs
                GROUP BY status ORDER BY status
                """
            )
        )
    ).mappings().all()
    sources = (
        await conn.execute(
            sa.text(
                """
                SELECT source_system, resource_type, COUNT(*) AS count
                FROM source_records
                GROUP BY source_system, resource_type
                ORDER BY source_system, resource_type
                """
            )
        )
    ).mappings().all()
    return {
        "facts": dict(facts),
        "data_quality": [dict(row) for row in quality],
        "quality_gate": dict(quality_gate),
        "enrichment_queue": [dict(row) for row in enrichments],
        "geospatial_queue": [dict(row) for row in geospatial],
        "source_records": [dict(row) for row in sources],
    }


def _verdict(
    stages: dict[str, dict[str, Any]],
    coverage: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons = [
        f"stage_failed:{name}"
        for name, stage in stages.items()
        if stage["status"] == "FAILED"
    ]
    if reasons:
        return "FAILED", reasons
    quality_gate = coverage.get("quality_gate")
    if quality_gate is None:
        blocking_quality = sum(
            int(row["count"])
            for row in coverage["data_quality"]
            if row["severity"] in ("ERROR", "BLOCKING")
            and row["issue_code"] != "INCOMPLETE_ADAMCHAIN_PLACEHOLDER"
        )
    else:
        blocking_quality = int(quality_gate.get("unquarantined_errors", 0))
    pending_enrichments = sum(
        int(row["count"])
        for row in coverage["enrichment_queue"]
        if row["status"] in ("QUEUED", "FAILED", "RUNNING")
    )
    pending_geospatial = sum(
        int(row["count"])
        for row in coverage["geospatial_queue"]
        if row["status"] in ("QUEUED", "RUNNING")
    )
    if blocking_quality:
        reasons.append(f"open_quality_errors:{blocking_quality}")
    if pending_enrichments:
        reasons.append(f"pending_enrichments:{pending_enrichments}")
    if pending_geospatial:
        reasons.append(f"pending_geospatial:{pending_geospatial}")
    return ("PARTIAL", reasons) if reasons else ("COMPLETE", [])


async def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    engine = create_async_engine(_async_url(args.database_url))
    stages: dict[str, dict[str, Any]] = {}
    try:
        async with engine.connect() as conn:
            if not args.skip_quality:
                await _stage(
                    conn,
                    stages,
                    "data_quality",
                    lambda: run_data_quality_checks(conn),
                )
            if not args.skip_competition:
                await _stage(
                    conn,
                    stages,
                    "competition",
                    lambda: backfill_winner_participations(conn),
                )
            if args.geospatial_limit > 0:
                async def geospatial() -> dict[str, int]:
                    remaining = args.geospatial_limit
                    counts: dict[str, int] = {}
                    config = GeocoderConfig.from_env()
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

                await _stage(conn, stages, "geospatial", geospatial)
            if not args.skip_marts:
                await _stage(
                    conn,
                    stages,
                    "analytics_marts",
                    lambda: refresh_all_marts(conn),
                )
            if not args.skip_scoring:
                async def scoring() -> dict[str, int]:
                    tenant_ids = await tenant_ids_with_business_profiles(conn)
                    scores = 0
                    for tenant_id in tenant_ids:
                        result = await score_opportunities_for_tenant(
                            conn,
                            tenant_id=tenant_id,
                            lookback_days=args.scoring_lookback_days,
                        )
                        scores += result.scores_written
                    return {"tenants": len(tenant_ids), "scores_written": scores}

                await _stage(conn, stages, "opportunity_scoring", scoring)
            if not args.skip_search:
                async def search() -> Any:
                    config = OpenSearchConfig.from_env()
                    async with httpx.AsyncClient(
                        timeout=config.request_timeout_seconds
                    ) as client:
                        return await rebuild_all_indexes_atomic(
                            conn,
                            client,
                            config,
                            batch_size=args.search_batch_size,
                        )

                await _stage(conn, stages, "search_rebuild", search)
            coverage = await _coverage(conn)
    finally:
        await engine.dispose()

    verdict, reasons = _verdict(stages, coverage)
    report = {
        "verdict": verdict,
        "reasons": reasons,
        "stages": stages,
        "coverage": coverage,
    }
    return report, 1 if verdict == "FAILED" else 2 if verdict == "PARTIAL" else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--geospatial-limit", type=int, default=0)
    parser.add_argument("--geospatial-batch-size", type=int, default=250)
    parser.add_argument("--scoring-lookback-days", type=int, default=365)
    parser.add_argument("--search-batch-size", type=int, default=500)
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--skip-competition", action="store_true")
    parser.add_argument("--skip-marts", action="store_true")
    parser.add_argument("--skip-scoring", action="store_true")
    parser.add_argument("--skip-search", action="store_true")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.geospatial_limit < 0 or args.geospatial_batch_size <= 0:
        parser.error("geospatial limit must be non-negative and batch size positive")
    if args.scoring_lookback_days <= 0 or args.search_batch_size <= 0:
        parser.error("scoring lookback and search batch size must be positive")
    report, exit_code = asyncio.run(_run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"coverage report: {args.report_path}")
    print(rendered)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

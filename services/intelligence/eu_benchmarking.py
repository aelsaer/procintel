"""Persisted TED country cohorts and tenant-relative cross-border matches."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    business_profiles,
    eu_benchmark_snapshots,
    tenant_cross_border_matches,
    tenants,
)

from .eu_matching import cross_border_match


@dataclass(frozen=True)
class CrossBorderRefresh:
    tenant_id: uuid.UUID
    profile_version: int
    candidates_seen: int
    matches_written: int


async def refresh_eu_benchmark_snapshots(
    conn: AsyncConnection,
    *,
    date_from: date,
    date_to: date,
    snapshot_date: date | None = None,
) -> int:
    snapshot = snapshot_date or datetime.now(timezone.utc).date()
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH notice_cpvs AS (
                    SELECT DISTINCT
                        detail.act_id,
                        detail.country_code,
                        LEFT(cpv.cpv_code, 2) AS cpv_prefix,
                        detail.awarded_value,
                        detail.estimated_value,
                        detail.parse_confidence,
                        act.submission_deadline
                    FROM ted_notice_details detail
                    JOIN procurement_acts act ON act.id = detail.act_id
                    JOIN act_cpv_codes cpv ON cpv.act_id = detail.act_id
                    WHERE detail.is_latest_version
                      AND detail.country_code IS NOT NULL
                      AND act.publication_date BETWEEN :date_from AND :date_to
                      AND LENGTH(cpv.cpv_code) >= 2
                )
                SELECT
                    country_code,
                    cpv_prefix,
                    COUNT(DISTINCT act_id)::INTEGER AS notice_count,
                    COUNT(DISTINCT act_id) FILTER (WHERE awarded_value IS NOT NULL)::INTEGER AS award_count,
                    COALESCE(SUM(COALESCE(awarded_value, estimated_value)), 0) AS total_value,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY COALESCE(awarded_value, estimated_value)
                    ) FILTER (
                        WHERE COALESCE(awarded_value, estimated_value) IS NOT NULL
                    ) AS median_value,
                    COUNT(DISTINCT act_id) FILTER (
                        WHERE COALESCE(awarded_value, estimated_value) IS NOT NULL
                    )::INTEGER AS valued_notice_count,
                    COUNT(DISTINCT act_id) FILTER (
                        WHERE submission_deadline IS NOT NULL
                    )::INTEGER AS deadline_notice_count,
                    AVG(parse_confidence) AS average_parse_confidence
                FROM notice_cpvs
                GROUP BY country_code, cpv_prefix
                """
            ),
            {"date_from": date_from, "date_to": date_to},
        )
    ).all()

    await conn.execute(
        eu_benchmark_snapshots.delete().where(
            eu_benchmark_snapshots.c.snapshot_date == snapshot,
            eu_benchmark_snapshots.c.date_from == date_from,
            eu_benchmark_snapshots.c.date_to == date_to,
        )
    )
    for row in rows:
        dimensions = {
            "value_basis": "awarded_value_else_estimated_value",
            "valued_notice_count": row.valued_notice_count,
            "deadline_notice_count": row.deadline_notice_count,
            "average_parse_confidence": (
                float(row.average_parse_confidence)
                if row.average_parse_confidence is not None
                else None
            ),
            "source": "TED Search API v3",
        }
        await conn.execute(
            pg_insert(eu_benchmark_snapshots)
            .values(
                id=uuid.uuid4(),
                snapshot_date=snapshot,
                date_from=date_from,
                date_to=date_to,
                cpv_prefix=row.cpv_prefix,
                country_code=row.country_code,
                notice_count=row.notice_count,
                award_count=row.award_count,
                total_value=row.total_value,
                median_value=row.median_value,
                dimensions=dimensions,
                computed_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                index_elements=[
                    eu_benchmark_snapshots.c.snapshot_date,
                    eu_benchmark_snapshots.c.date_from,
                    eu_benchmark_snapshots.c.date_to,
                    eu_benchmark_snapshots.c.cpv_prefix,
                    eu_benchmark_snapshots.c.country_code,
                ],
                set_={
                    "notice_count": row.notice_count,
                    "award_count": row.award_count,
                    "total_value": row.total_value,
                    "median_value": row.median_value,
                    "dimensions": dimensions,
                    "computed_at": datetime.now(timezone.utc),
                },
            )
        )
    return len(rows)


async def refresh_cross_border_matches_for_tenant(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    as_of: date | None = None,
    lookback_days: int = 120,
    limit: int = 500,
    publication_from: date | None = None,
    publication_to: date | None = None,
) -> CrossBorderRefresh:
    today = as_of or datetime.now(timezone.utc).date()
    profile = (
        await conn.execute(
            sa.select(business_profiles).where(business_profiles.c.tenant_id == tenant_id)
        )
    ).first()
    if profile is None:
        await conn.execute(
            tenant_cross_border_matches.delete().where(
                tenant_cross_border_matches.c.tenant_id == tenant_id
            )
        )
        return CrossBorderRefresh(tenant_id, 1, 0, 0)

    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT
                    act.id AS act_id,
                    act.process_id,
                    act.title,
                    act.submission_deadline,
                    detail.country_code,
                    detail.parse_confidence,
                    detail.estimated_value,
                    detail.publication_number,
                    detail.ted_notice_id,
                    COALESCE(cpvs.codes, ARRAY[]::TEXT[]) AS cpv_codes
                FROM ted_notice_details detail
                JOIN procurement_acts act ON act.id = detail.act_id
                LEFT JOIN LATERAL (
                    SELECT ARRAY_AGG(DISTINCT cpv.cpv_code ORDER BY cpv.cpv_code) AS codes
                    FROM act_cpv_codes cpv
                    WHERE cpv.act_id = act.id
                ) cpvs ON TRUE
                WHERE detail.is_latest_version
                  AND detail.country_code IS NOT NULL
                  AND detail.country_code <> 'GR'
                  AND detail.awarded_value IS NULL
                  AND act.is_current
                  AND (
                        CAST(:publication_from AS DATE) IS NULL
                        OR act.publication_date >= CAST(:publication_from AS DATE)
                  )
                  AND (
                        CAST(:publication_to AS DATE) IS NULL
                        OR act.publication_date <= CAST(:publication_to AS DATE)
                  )
                  AND (
                        act.submission_deadline >= CAST(:as_of AS DATE)
                        OR (
                            act.submission_deadline IS NULL
                            AND act.publication_date >= CAST(:since_date AS DATE)
                        )
                  )
                ORDER BY act.submission_deadline ASC NULLS LAST,
                         act.publication_date DESC NULLS LAST
                LIMIT :candidate_limit
                """
            ),
            {
                "as_of": today,
                "since_date": today - timedelta(days=lookback_days),
                "publication_from": publication_from,
                "publication_to": publication_to,
                "candidate_limit": max(limit * 8, 1000),
            },
        )
    ).all()

    profile_version = int(profile.classification_version)
    ranked: list[tuple[Decimal, object, list[str], list[str]]] = []
    for row in rows:
        score, reasons, barriers, eligible = cross_border_match(
            title=row.title,
            cpv_codes=row.cpv_codes or [],
            profile_cpv_prefixes=profile.cpv_prefixes or [],
            profile_keywords=profile.keywords or [],
            amount=row.estimated_value,
            amount_min=Decimal(str(profile.amount_min)) if profile.amount_min is not None else None,
            amount_max=Decimal(str(profile.amount_max)) if profile.amount_max is not None else None,
            deadline=row.submission_deadline,
            country_code=row.country_code,
            parse_confidence=float(row.parse_confidence or 0),
            as_of=today,
        )
        if eligible:
            ranked.append((score, row, reasons, barriers))
    ranked.sort(
        key=lambda item: (
            item[0],
            item[1].submission_deadline or datetime.max.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    ranked = ranked[:limit]

    await conn.execute(
        tenant_cross_border_matches.delete().where(
            tenant_cross_border_matches.c.tenant_id == tenant_id
        )
    )
    now = datetime.now(timezone.utc)
    for score, row, reasons, barriers in ranked:
        await conn.execute(
            tenant_cross_border_matches.insert().values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                process_id=row.process_id,
                act_id=row.act_id,
                profile_version=profile_version,
                country_code=row.country_code,
                match_score=score,
                reasons=reasons,
                barriers=barriers,
                computed_at=now,
            )
        )
    return CrossBorderRefresh(
        tenant_id=tenant_id,
        profile_version=profile_version,
        candidates_seen=len(rows),
        matches_written=len(ranked),
    )


async def refresh_all_cross_border_matches(
    conn: AsyncConnection,
    *,
    as_of: date | None = None,
) -> list[CrossBorderRefresh]:
    tenant_ids = [row.id for row in (await conn.execute(sa.select(tenants.c.id))).all()]
    return [
        await refresh_cross_border_matches_for_tenant(
            conn,
            tenant_id=tenant_id,
            as_of=as_of,
        )
        for tenant_id in tenant_ids
    ]

"""Authenticated European TED benchmarking and opportunity matching."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    business_profiles,
    eu_benchmark_snapshots,
    tenant_cross_border_matches,
)
from services.intelligence.eu_benchmarking import (
    refresh_cross_border_matches_for_tenant,
    refresh_eu_benchmark_snapshots,
)
from services.intelligence.eu_matching import COUNTRY_NAMES_EL, official_ted_url

from ..auth import get_current_user
from ..db import get_tenant_scoped_conn
from ..workspace import tenant_uuid

router = APIRouter(prefix="/v1/europe", tags=["europe"])


class EuropeanBenchmarkResponse(BaseModel):
    country_code: str
    country_name: str
    cpv_prefix: str
    notice_count: int
    award_count: int
    total_value: Decimal
    median_value: Decimal | None
    valued_notice_count: int
    deadline_notice_count: int
    average_parse_confidence: float | None


class EuropeanBenchmarkListResponse(BaseModel):
    generated_at: datetime
    date_from: date
    date_to: date
    cpv_prefixes: list[str]
    covered_countries: int
    rows: list[EuropeanBenchmarkResponse]
    methodology: list[str]


class CrossBorderOpportunityResponse(BaseModel):
    act_id: str
    process_id: str | None
    ted_notice_id: str
    publication_number: str | None
    official_url: str
    title: str
    buyer_name: str | None
    country_code: str
    country_name: str
    cpv_codes: list[str]
    estimated_value: Decimal | None
    currency: str
    publication_date: date | None
    submission_deadline: datetime | None
    match_score: Decimal
    reasons: list[str]
    barriers: list[str]
    parse_confidence: float
    computed_at: datetime


class CrossBorderOpportunityListResponse(BaseModel):
    generated_at: datetime
    profile_version: int
    candidates_seen: int
    matches: list[CrossBorderOpportunityResponse]
    methodology: list[str]


def _csv(value: str | None) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in (value or "").split(",") if part.strip()))


@router.get("/benchmarks", response_model=EuropeanBenchmarkListResponse)
async def european_benchmarks(
    date_from: date = Query(default_factory=lambda: date.today() - timedelta(days=365)),
    date_to: date = Query(default_factory=date.today),
    cpv_prefixes: str | None = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> EuropeanBenchmarkListResponse:
    requested_cpvs = [value[:2] for value in _csv(cpv_prefixes) if len(value) >= 2]
    if not requested_cpvs:
        profile = (
            await conn.execute(
                sa.select(business_profiles.c.cpv_prefixes).where(
                    business_profiles.c.tenant_id == tenant_uuid(user)
                )
            )
        ).first()
        requested_cpvs = list(
            dict.fromkeys(value[:2] for value in (profile.cpv_prefixes if profile else []) if len(value) >= 2)
        )

    snapshot = datetime.now(timezone.utc).date()
    existing = (
        await conn.execute(
            sa.select(sa.func.count())
            .select_from(eu_benchmark_snapshots)
            .where(
                eu_benchmark_snapshots.c.snapshot_date == snapshot,
                eu_benchmark_snapshots.c.date_from == date_from,
                eu_benchmark_snapshots.c.date_to == date_to,
            )
        )
    ).scalar_one()
    if not existing:
        await refresh_eu_benchmark_snapshots(
            conn,
            date_from=date_from,
            date_to=date_to,
            snapshot_date=snapshot,
        )

    query = sa.select(eu_benchmark_snapshots).where(
        eu_benchmark_snapshots.c.snapshot_date == snapshot,
        eu_benchmark_snapshots.c.date_from == date_from,
        eu_benchmark_snapshots.c.date_to == date_to,
    )
    if requested_cpvs:
        query = query.where(eu_benchmark_snapshots.c.cpv_prefix.in_(requested_cpvs))
    rows = (
        await conn.execute(
            query.order_by(
                eu_benchmark_snapshots.c.notice_count.desc(),
                eu_benchmark_snapshots.c.country_code,
            )
        )
    ).all()
    response_rows = []
    for row in rows:
        dimensions = row.dimensions if isinstance(row.dimensions, dict) else {}
        response_rows.append(
            EuropeanBenchmarkResponse(
                country_code=row.country_code,
                country_name=COUNTRY_NAMES_EL.get(row.country_code, row.country_code),
                cpv_prefix=row.cpv_prefix,
                notice_count=row.notice_count,
                award_count=row.award_count,
                total_value=row.total_value,
                median_value=row.median_value,
                valued_notice_count=int(dimensions.get("valued_notice_count") or 0),
                deadline_notice_count=int(dimensions.get("deadline_notice_count") or 0),
                average_parse_confidence=dimensions.get("average_parse_confidence"),
            )
        )
    return EuropeanBenchmarkListResponse(
        generated_at=datetime.now(timezone.utc),
        date_from=date_from,
        date_to=date_to,
        cpv_prefixes=requested_cpvs,
        covered_countries=len({row.country_code for row in rows}),
        rows=response_rows,
        methodology=[
            "Source: TED Search API v3; only latest notice versions are counted.",
            "Value is awarded value when published, otherwise estimated value; coverage is shown separately.",
            "Cohorts compare the first two CPV digits and never infer bidder, SME or single-bid facts.",
        ],
    )


@router.get("/opportunities", response_model=CrossBorderOpportunityListResponse)
async def cross_border_opportunities(
    limit: int = Query(default=100, ge=1, le=500),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> CrossBorderOpportunityListResponse:
    refresh = await refresh_cross_border_matches_for_tenant(
        conn,
        tenant_id=tenant_uuid(user),
        as_of=date_to,
        limit=limit,
        publication_from=date_from,
        publication_to=date_to,
    )
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT
                    match.act_id,
                    match.process_id,
                    match.country_code,
                    match.match_score,
                    match.reasons,
                    match.barriers,
                    match.computed_at,
                    act.title,
                    act.publication_date,
                    act.submission_deadline,
                    detail.ted_notice_id,
                    detail.publication_number,
                    detail.estimated_value,
                    detail.currency,
                    detail.parse_confidence,
                    COALESCE(cpvs.codes, ARRAY[]::TEXT[]) AS cpv_codes,
                    COALESCE(process_buyer.canonical_name, act_buyer.canonical_name) AS buyer_name
                FROM tenant_cross_border_matches match
                JOIN procurement_acts act ON act.id = match.act_id
                JOIN ted_notice_details detail ON detail.act_id = act.id
                LEFT JOIN procurement_processes process ON process.id = match.process_id
                LEFT JOIN entities process_buyer ON process_buyer.id = process.buyer_entity_id
                LEFT JOIN LATERAL (
                    SELECT entity.canonical_name
                    FROM act_parties party
                    JOIN entities entity ON entity.id = party.entity_id
                    WHERE party.act_id = act.id
                      AND party.party_role = 'BUYER'
                    LIMIT 1
                ) act_buyer ON TRUE
                LEFT JOIN LATERAL (
                    SELECT ARRAY_AGG(DISTINCT cpv.cpv_code ORDER BY cpv.cpv_code) AS codes
                    FROM act_cpv_codes cpv
                    WHERE cpv.act_id = act.id
                ) cpvs ON TRUE
                WHERE match.tenant_id = CAST(:tenant_id AS UUID)
                  AND match.profile_version = :profile_version
                ORDER BY match.match_score DESC,
                         act.submission_deadline ASC NULLS LAST
                LIMIT :limit
                """
            ),
            {
                "tenant_id": str(tenant_uuid(user)),
                "profile_version": refresh.profile_version,
                "limit": limit,
            },
        )
    ).all()
    return CrossBorderOpportunityListResponse(
        generated_at=datetime.now(timezone.utc),
        profile_version=refresh.profile_version,
        candidates_seen=refresh.candidates_seen,
        matches=[
            CrossBorderOpportunityResponse(
                act_id=str(row.act_id),
                process_id=str(row.process_id) if row.process_id else None,
                ted_notice_id=row.ted_notice_id,
                publication_number=row.publication_number,
                official_url=official_ted_url(row.publication_number, row.ted_notice_id),
                title=row.title or row.publication_number or row.ted_notice_id,
                buyer_name=row.buyer_name,
                country_code=row.country_code,
                country_name=COUNTRY_NAMES_EL.get(row.country_code, row.country_code),
                cpv_codes=row.cpv_codes or [],
                estimated_value=row.estimated_value,
                currency=row.currency or "EUR",
                publication_date=row.publication_date,
                submission_deadline=row.submission_deadline,
                match_score=row.match_score,
                reasons=row.reasons if isinstance(row.reasons, list) else [],
                barriers=row.barriers if isinstance(row.barriers, list) else [],
                parse_confidence=float(row.parse_confidence or 0),
                computed_at=row.computed_at,
            )
            for row in rows
        ],
        methodology=[
            "Matches require a profile CPV or business-term hit; broad CPVs require title evidence.",
            "Scores measure commercial fit and data confidence, not probability of winning.",
            "Every record links to the official TED notice and exposes cross-border review barriers.",
        ],
    )

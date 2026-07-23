"""Tenant-persisted business profile and explainable classification."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import business_profile_terms, business_profiles, opportunity_score_jobs
from services.analytics.profile_classification import CpvCatalogEntry, ClassifiedTerm, classify_business_description
from services.analytics.scoring_worker import process_scoring_job_by_tenant

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/business-profile", tags=["workspace"])
_WRITE_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")
_cpv_catalog_cache: tuple[CpvCatalogEntry, ...] = ()
_cpv_catalog_count = -1


class ProfileTermResponse(BaseModel):
    id: str | None = None
    term_type: str
    value: str
    label: str
    confidence: float
    reason: str
    source: str = "RULE"
    is_active: bool = True


class BusinessProfileResponse(BaseModel):
    id: str
    company_name: str | None = None
    description: str
    cpv_prefixes: list[str]
    keywords: list[str]
    nuts_codes: list[str]
    municipality: str | None = None
    buyer_types: list[str]
    procedure_types: list[str]
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    classification_version: int
    classified_at: datetime | None = None
    updated_at: datetime
    terms: list[ProfileTermResponse]


class BusinessProfileUpdate(BaseModel):
    company_name: str | None = Field(default=None, max_length=250)
    description: str = Field(default="", max_length=10_000)
    cpv_prefixes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    nuts_codes: list[str] = Field(default_factory=list)
    municipality: str | None = Field(default=None, max_length=160)
    buyer_types: list[str] = Field(default_factory=list)
    procedure_types: list[str] = Field(default_factory=list)
    amount_min: Decimal | None = Field(default=None, ge=0)
    amount_max: Decimal | None = Field(default=None, ge=0)
    classify: bool = True


class OpportunityScoringStatusResponse(BaseModel):
    status: str
    reason: str | None = None
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None


class ClassificationRequest(BaseModel):
    description: str = Field(min_length=3, max_length=10_000)


def _term_response(term: ClassifiedTerm) -> ProfileTermResponse:
    return ProfileTermResponse(
        term_type=term.term_type,
        value=term.value,
        label=term.label,
        confidence=term.confidence,
        reason=term.reason,
        source=term.source,
    )


async def _load_cpv_catalog(conn: AsyncConnection) -> tuple[CpvCatalogEntry, ...]:
    global _cpv_catalog_cache, _cpv_catalog_count
    count = int((await conn.execute(sa.text("SELECT COUNT(*) FROM cpv_codes"))).scalar_one())
    if count == _cpv_catalog_count:
        return _cpv_catalog_cache
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT code, description_el, description_en, parent_code
                FROM cpv_codes
                ORDER BY code
                """
            )
        )
    ).all()
    _cpv_catalog_cache = tuple(
        CpvCatalogEntry(
            code=row.code,
            description_el=row.description_el,
            description_en=row.description_en,
            parent_code=row.parent_code,
        )
        for row in rows
    )
    _cpv_catalog_count = count
    return _cpv_catalog_cache


async def _classify(conn: AsyncConnection, description: str) -> list[ClassifiedTerm]:
    return classify_business_description(description, await _load_cpv_catalog(conn))


async def _serialize(conn: AsyncConnection, profile_id: uuid.UUID) -> BusinessProfileResponse:
    row = (await conn.execute(sa.select(business_profiles).where(business_profiles.c.id == profile_id))).one()
    term_rows = (
        await conn.execute(
            sa.select(business_profile_terms)
            .where(business_profile_terms.c.profile_id == profile_id)
            .order_by(business_profile_terms.c.confidence.desc(), business_profile_terms.c.label)
        )
    ).all()
    return BusinessProfileResponse(
        id=str(row.id),
        company_name=row.company_name,
        description=row.description,
        cpv_prefixes=row.cpv_prefixes or [],
        keywords=row.keywords or [],
        nuts_codes=row.nuts_codes or [],
        municipality=row.municipality,
        buyer_types=row.buyer_types or [],
        procedure_types=row.procedure_types or [],
        amount_min=row.amount_min,
        amount_max=row.amount_max,
        classification_version=row.classification_version,
        classified_at=row.classified_at,
        updated_at=row.updated_at,
        terms=[
            ProfileTermResponse(
                id=str(term.id), term_type=term.term_type, value=term.value, label=term.label,
                confidence=float(term.confidence), reason=term.reason, source=term.source,
                is_active=term.is_active,
            )
            for term in term_rows
        ],
    )


@router.post("/classify", response_model=list[ProfileTermResponse])
async def classify_profile(
    body: ClassificationRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    _: AuthenticatedUser = Depends(get_current_user),
) -> list[ProfileTermResponse]:
    return [_term_response(term) for term in await _classify(conn, body.description)]


@router.get("", response_model=BusinessProfileResponse)
async def get_business_profile(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> BusinessProfileResponse:
    tenant_id = tenant_uuid(user)
    profile_id = (await conn.execute(sa.select(business_profiles.c.id).where(business_profiles.c.tenant_id == tenant_id))).scalar_one_or_none()
    if profile_id is None:
        user_id = await ensure_workspace_user(conn, user)
        profile_id = uuid.uuid4()
        await conn.execute(
            business_profiles.insert().values(id=profile_id, tenant_id=tenant_id, created_by=user_id)
        )
    return await _serialize(conn, profile_id)


@router.get("/scoring-status", response_model=OpportunityScoringStatusResponse)
async def get_opportunity_scoring_status(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OpportunityScoringStatusResponse:
    row = (
        await conn.execute(
            sa.select(opportunity_score_jobs).where(
                opportunity_score_jobs.c.tenant_id == tenant_uuid(user)
            )
        )
    ).first()
    if row is None:
        return OpportunityScoringStatusResponse(status="IDLE")
    return OpportunityScoringStatusResponse(
        status=row.status,
        reason=row.reason,
        requested_at=row.requested_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
    )


@router.put("", response_model=BusinessProfileResponse)
async def update_business_profile(
    body: BusinessProfileUpdate, background_tasks: BackgroundTasks,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> BusinessProfileResponse:
    if body.amount_min is not None and body.amount_max is not None and body.amount_min > body.amount_max:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="amount_min must not exceed amount_max")

    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    now = datetime.now(timezone.utc)
    classified = await _classify(conn, body.description) if body.classify else []
    inferred_cpv = [
        term.value for term in classified
        if term.term_type == "CPV_PREFIX" and term.confidence >= 0.8
    ]
    inferred_keywords = [term.value for term in classified if term.term_type == "KEYWORD"]
    cpv_prefixes = list(dict.fromkeys(body.cpv_prefixes or inferred_cpv))
    keywords = list(dict.fromkeys(body.keywords or inferred_keywords))
    profile_id = uuid.uuid4()

    await conn.execute(
        pg_insert(business_profiles)
        .values(
            id=profile_id, tenant_id=tenant_id, created_by=user_id,
            company_name=body.company_name, description=body.description,
            cpv_prefixes=cpv_prefixes, keywords=keywords, nuts_codes=body.nuts_codes,
            municipality=body.municipality, buyer_types=body.buyer_types,
            procedure_types=body.procedure_types, amount_min=body.amount_min,
            amount_max=body.amount_max, classified_at=now, updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[business_profiles.c.tenant_id],
            set_={
                "company_name": body.company_name, "description": body.description,
                "cpv_prefixes": cpv_prefixes, "keywords": keywords, "nuts_codes": body.nuts_codes,
                "municipality": body.municipality, "buyer_types": body.buyer_types,
                "procedure_types": body.procedure_types, "amount_min": body.amount_min,
                "amount_max": body.amount_max, "classified_at": now, "updated_at": now,
                "classification_version": business_profiles.c.classification_version + 1,
            },
        )
    )
    actual_profile_id = (await conn.execute(sa.select(business_profiles.c.id).where(business_profiles.c.tenant_id == tenant_id))).scalar_one()
    if body.classify:
        await conn.execute(business_profile_terms.delete().where(business_profile_terms.c.profile_id == actual_profile_id))
        for term in classified:
            await conn.execute(
                business_profile_terms.insert().values(
                    id=uuid.uuid4(), profile_id=actual_profile_id, term_type=term.term_type,
                    value=term.value, label=term.label, confidence=term.confidence,
                    reason=term.reason, source=term.source,
                    is_active=(
                        term.value in cpv_prefixes
                        if term.term_type == "CPV_PREFIX"
                        else term.value in keywords
                    ),
                )
            )
    await conn.execute(
        pg_insert(opportunity_score_jobs)
        .values(tenant_id=tenant_id, status="QUEUED", reason="BUSINESS_PROFILE_CHANGED", requested_at=now)
        .on_conflict_do_update(
            index_elements=[opportunity_score_jobs.c.tenant_id],
            set_={"status": "QUEUED", "reason": "BUSINESS_PROFILE_CHANGED", "requested_at": now, "error": None},
        )
    )
    response = await _serialize(conn, actual_profile_id)
    await conn.commit()
    background_tasks.add_task(process_scoring_job_by_tenant, tenant_id)
    return response

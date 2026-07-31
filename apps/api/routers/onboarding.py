"""Guided first-session onboarding with immediate, strict opportunity matches."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    alert_delivery_targets,
    alert_rules,
    business_profiles,
    onboarding_sessions,
    profile_review_requests,
)
from services.analytics.scoring_worker import process_scoring_job_by_tenant
from services.intelligence.tender_brief import links_for_display_identifier
from services.product.onboarding import (
    normalize_cpv_codes,
    normalize_terms,
    profile_quality,
)

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..workspace import ensure_workspace_user, tenant_uuid
from .business_profiles import (
    BusinessProfileUpdate,
    ProfileTermResponse,
    _classify,
    _term_response,
    update_business_profile,
)

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])
_WRITE_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")


class OnboardingStatusResponse(BaseModel):
    required: bool
    session_id: str | None = None
    status: str
    current_step: str
    description: str = ""
    selected_cpv_codes: list[str] = Field(default_factory=list)
    selected_keywords: list[str] = Field(default_factory=list)
    selected_nuts_codes: list[str] = Field(default_factory=list)
    quality_score: float | None = None
    quality_findings: list[dict[str, Any]] = Field(default_factory=list)


class OnboardingSuggestionRequest(BaseModel):
    company_description: str = Field(min_length=12, max_length=10_000)


class OnboardingSuggestionResponse(BaseModel):
    session_id: str
    cpv_suggestions: list[ProfileTermResponse]
    keyword_suggestions: list[ProfileTermResponse]


class OnboardingCompleteRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=250)
    company_description: str = Field(min_length=12, max_length=10_000)
    selected_cpv_codes: list[str] = Field(min_length=1, max_length=50)
    selected_keywords: list[str] = Field(default_factory=list, max_length=50)
    selected_nuts_codes: list[str] = Field(default_factory=list, max_length=20)
    municipality: str | None = Field(default=None, max_length=160)
    amount_min: Decimal | None = Field(default=None, ge=0)
    amount_max: Decimal | None = Field(default=None, ge=0)
    request_human_review: bool = False
    review_notes: str | None = Field(default=None, max_length=2000)


class InitialOpportunityResponse(BaseModel):
    process_id: str
    title: str
    buyer_name: str | None = None
    amount: Decimal | None = None
    deadline: datetime | None = None
    score: float
    data_confidence: float
    cpv_codes: list[str]
    locations: list[str]
    adam: str | None = None
    official_url: str | None = None
    document_url: str | None = None
    reasons: list[dict[str, Any]]


class OnboardingCompleteResponse(BaseModel):
    session_id: str
    profile_id: str
    quality_score: float
    quality_findings: list[dict[str, Any]]
    review_status: str | None = None
    opportunities: list[InitialOpportunityResponse]


async def _active_session(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Any | None:
    return (
        await conn.execute(
            sa.select(onboarding_sessions)
            .where(
                onboarding_sessions.c.tenant_id == tenant_id,
                onboarding_sessions.c.user_id == user_id,
                onboarding_sessions.c.status != "COMPLETED",
            )
            .order_by(onboarding_sessions.c.updated_at.desc())
            .limit(1)
        )
    ).first()


async def _load_initial_opportunities(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    cpv_codes: list[str],
    keywords: list[str],
    limit: int = 10,
) -> list[InitialOpportunityResponse]:
    cpv_likes = [f"{code}%" for code in cpv_codes]
    keyword_likes = [f"%{term}%" for term in keywords]
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH profile AS (
                    SELECT classification_version
                    FROM business_profiles
                    WHERE tenant_id = CAST(:tenant_id AS UUID)
                ),
                candidate_acts AS (
                    SELECT DISTINCT ON (act.process_id)
                        act.id,
                        act.process_id,
                        act.title,
                        COALESCE(act.amount_gross, act.amount_net) AS amount,
                        act.submission_deadline AS deadline,
                        COALESCE(
                            act.publication_date,
                            act.submission_date,
                            act.decision_date
                        ) AS event_date
                    FROM procurement_acts act
                    WHERE act.is_current = TRUE
                      AND act.process_id IS NOT NULL
                      AND act.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM act_cpv_codes cpv
                              WHERE cpv.act_id = act.id
                                AND cpv.cpv_code LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                          )
                          OR (
                              CARDINALITY(CAST(:keyword_likes AS TEXT[])) > 0
                              AND act.title ILIKE ANY(CAST(:keyword_likes AS TEXT[]))
                          )
                      )
                    ORDER BY act.process_id, event_date DESC NULLS LAST, act.id
                )
                SELECT
                    candidate.process_id,
                    process.title,
                    buyer.canonical_name AS buyer_name,
                    COALESCE(process.estimated_value, candidate.amount) AS amount,
                    candidate.deadline,
                    COALESCE(score.total_score, 50) AS score,
                    COALESCE(score.data_confidence_score, 35) AS data_confidence,
                    COALESCE(score.evidence, '{}'::jsonb) AS score_evidence,
                    identifiers.adam,
                    COALESCE(cpvs.codes, ARRAY[]::TEXT[]) AS cpv_codes,
                    COALESCE(locations.labels, ARRAY[]::TEXT[]) AS locations
                FROM candidate_acts candidate
                JOIN procurement_processes process ON process.id = candidate.process_id
                LEFT JOIN entities buyer ON buyer.id = process.buyer_entity_id
                LEFT JOIN profile ON TRUE
                LEFT JOIN opportunity_scores score
                  ON score.process_id = candidate.process_id
                 AND score.tenant_id = CAST(:tenant_id AS UUID)
                 AND score.profile_version = profile.classification_version
                LEFT JOIN LATERAL (
                    SELECT MAX(identifier.value_normalized)
                        FILTER (WHERE identifier.scheme = 'ADAM') AS adam
                    FROM procurement_acts member
                    JOIN act_identifiers identifier ON identifier.act_id = member.id
                    WHERE member.process_id = candidate.process_id
                ) identifiers ON TRUE
                LEFT JOIN LATERAL (
                    SELECT ARRAY_AGG(DISTINCT cpv.cpv_code ORDER BY cpv.cpv_code) AS codes
                    FROM procurement_acts member
                    JOIN act_cpv_codes cpv ON cpv.act_id = member.id
                    WHERE member.process_id = candidate.process_id
                ) cpvs ON TRUE
                LEFT JOIN LATERAL (
                    SELECT ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(
                        location.municipality_name,
                        location.regional_unit_name,
                        location.region_name,
                        location.place_text
                    )), NULL) AS labels
                    FROM procurement_acts member
                    JOIN act_locations location ON location.act_id = member.id
                    WHERE member.process_id = candidate.process_id
                ) locations ON TRUE
                ORDER BY
                    COALESCE(score.total_score, 50) DESC,
                    COALESCE(score.data_confidence_score, 35) DESC,
                    candidate.event_date DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "cpv_likes": cpv_likes,
                "keyword_likes": keyword_likes,
                "limit": limit,
            },
        )
    ).all()
    opportunities: list[InitialOpportunityResponse] = []
    for row in rows:
        official_url, document_url = links_for_display_identifier("ADAM", row.adam)
        evidence = row.score_evidence if isinstance(row.score_evidence, dict) else {}
        reasons = evidence.get("reasons") or [
            {
                "code": "STRICT_PROFILE_MATCH",
                "label": "Αντιστοίχιση με επιβεβαιωμένο CPV ή ειδικό όρο",
            }
        ]
        opportunities.append(
            InitialOpportunityResponse(
                process_id=str(row.process_id),
                title=row.title,
                buyer_name=row.buyer_name,
                amount=row.amount,
                deadline=row.deadline,
                score=float(row.score),
                data_confidence=float(row.data_confidence),
                cpv_codes=list(row.cpv_codes or []),
                locations=list(row.locations or []),
                adam=row.adam,
                official_url=official_url,
                document_url=document_url,
                reasons=reasons if isinstance(reasons, list) else [],
            )
        )
    return opportunities


@router.get("/status", response_model=OnboardingStatusResponse)
async def onboarding_status(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OnboardingStatusResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    session = await _active_session(conn, tenant_id=tenant_id, user_id=user_id)
    if session is not None:
        return OnboardingStatusResponse(
            required=True,
            session_id=str(session.id),
            status=session.status,
            current_step=session.current_step,
            description=session.company_description,
            selected_cpv_codes=list(session.selected_cpv_codes or []),
            selected_keywords=list(session.selected_keywords or []),
            selected_nuts_codes=list(session.selected_nuts_codes or []),
            quality_score=float(session.quality_score) if session.quality_score is not None else None,
            quality_findings=list(session.quality_findings or []),
        )

    profile = (
        await conn.execute(
            sa.select(business_profiles).where(business_profiles.c.tenant_id == tenant_id)
        )
    ).first()
    if profile is not None and profile.description.strip() and profile.cpv_prefixes:
        return OnboardingStatusResponse(
            required=False,
            status="COMPLETED",
            current_step="RESULTS",
            description=profile.description,
            selected_cpv_codes=list(profile.cpv_prefixes or []),
            selected_keywords=list(profile.keywords or []),
            selected_nuts_codes=list(profile.nuts_codes or []),
        )

    session_id = uuid.uuid4()
    await conn.execute(
        onboarding_sessions.insert().values(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )
    await conn.commit()
    return OnboardingStatusResponse(
        required=True,
        session_id=str(session_id),
        status="DRAFT",
        current_step="COMPANY",
    )


@router.post("/suggest", response_model=OnboardingSuggestionResponse)
async def suggest_profile(
    body: OnboardingSuggestionRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> OnboardingSuggestionResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    session = await _active_session(conn, tenant_id=tenant_id, user_id=user_id)
    session_id = session.id if session is not None else uuid.uuid4()
    classified = await _classify(conn, body.company_description)
    cpv_suggestions = [
        _term_response(term)
        for term in classified
        if term.term_type == "CPV_PREFIX"
    ][:16]
    keyword_suggestions = [
        _term_response(term)
        for term in classified
        if term.term_type == "KEYWORD"
    ][:20]
    suggestion_payload = [
        suggestion.model_dump(mode="json") for suggestion in cpv_suggestions
    ]
    values = {
        "company_description": body.company_description,
        "cpv_suggestions": suggestion_payload,
        "status": "AWAITING_CONFIRMATION",
        "current_step": "CONFIRM_SCOPE",
        "updated_at": datetime.now(timezone.utc),
    }
    if session is None:
        await conn.execute(
            onboarding_sessions.insert().values(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                **values,
            )
        )
    else:
        await conn.execute(
            onboarding_sessions.update()
            .where(onboarding_sessions.c.id == session_id)
            .values(**values)
        )
    await conn.commit()
    return OnboardingSuggestionResponse(
        session_id=str(session_id),
        cpv_suggestions=cpv_suggestions,
        keyword_suggestions=keyword_suggestions,
    )


@router.post("/complete", response_model=OnboardingCompleteResponse)
async def complete_onboarding(
    body: OnboardingCompleteRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> OnboardingCompleteResponse:
    cpv_codes = normalize_cpv_codes(body.selected_cpv_codes)
    keywords = normalize_terms(body.selected_keywords)
    if not cpv_codes:
        raise HTTPException(status_code=422, detail="Confirm at least one valid CPV code")
    if body.amount_min is not None and body.amount_max is not None and body.amount_min > body.amount_max:
        raise HTTPException(status_code=422, detail="amount_min must not exceed amount_max")

    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    session = await _active_session(conn, tenant_id=tenant_id, user_id=user_id)
    session_id = session.id if session is not None else uuid.uuid4()
    if session is None:
        await conn.execute(
            onboarding_sessions.insert().values(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                company_description=body.company_description,
            )
        )
        await conn.commit()

    profile = await update_business_profile(
        BusinessProfileUpdate(
            company_name=body.company_name,
            description=body.company_description,
            cpv_prefixes=cpv_codes,
            keywords=keywords,
            nuts_codes=body.selected_nuts_codes,
            municipality=body.municipality,
            amount_min=body.amount_min,
            amount_max=body.amount_max,
            classify=False,
        ),
        conn=conn,
        user=user,
    )

    try:
        await process_scoring_job_by_tenant(tenant_id)
    except Exception:
        # The strict CPV/keyword fallback below still returns credible records;
        # the durable scoring job remains queued for the worker to retry.
        pass

    opportunities = await _load_initial_opportunities(
        conn,
        tenant_id=tenant_id,
        cpv_codes=cpv_codes,
        keywords=keywords,
        limit=10,
    )
    quality_score, findings = profile_quality(
        description=body.company_description,
        cpv_codes=cpv_codes,
        keywords=keywords,
        opportunity_count=len(opportunities),
    )
    now = datetime.now(timezone.utc)
    await conn.execute(
        onboarding_sessions.update()
        .where(onboarding_sessions.c.id == session_id)
        .values(
            business_profile_id=uuid.UUID(profile.id),
            status="COMPLETED",
            current_step="RESULTS",
            company_description=body.company_description,
            selected_cpv_codes=cpv_codes,
            selected_keywords=keywords,
            selected_nuts_codes=body.selected_nuts_codes,
            initial_opportunity_ids=[
                uuid.UUID(opportunity.process_id) for opportunity in opportunities
            ],
            quality_score=quality_score,
            quality_findings=findings,
            completed_at=now,
            updated_at=now,
        )
    )

    review_status = None
    if body.request_human_review:
        review_status = "OPEN"
        await conn.execute(
            profile_review_requests.insert().values(
                tenant_id=tenant_id,
                onboarding_session_id=session_id,
                business_profile_id=uuid.UUID(profile.id),
                requested_by=user_id,
                request_notes=body.review_notes,
                priority="NORMAL",
            )
        )

    existing_rule = (
        await conn.execute(
            sa.select(alert_rules.c.id).where(
                alert_rules.c.tenant_id == tenant_id,
                alert_rules.c.name == "Καθημερινές ευκαιρίες προφίλ",
            )
        )
    ).scalar_one_or_none()
    if existing_rule is None:
        rule_id = uuid.uuid4()
        await conn.execute(
            alert_rules.insert().values(
                id=rule_id,
                tenant_id=tenant_id,
                user_id=user_id,
                name="Καθημερινές ευκαιρίες προφίλ",
                event_types=["opportunity.created", "opportunity.updated"],
                filters={
                    "cpv_prefixes": cpv_codes,
                    "keywords": keywords,
                    "nuts_codes": body.selected_nuts_codes,
                },
                schedule="DAILY_DIGEST",
                delivery_channels=["EMAIL", "IN_APP"],
                timezone="Europe/Athens",
            )
        )
        if user.email:
            await conn.execute(
                alert_delivery_targets.insert().values(
                    id=uuid.uuid4(),
                    alert_rule_id=rule_id,
                    channel_type="EMAIL",
                    target=user.email,
                )
            )
    await conn.commit()
    return OnboardingCompleteResponse(
        session_id=str(session_id),
        profile_id=profile.id,
        quality_score=quality_score,
        quality_findings=findings,
        review_status=review_status,
        opportunities=opportunities,
    )

"""Committee-ready, evidence-backed BID/NO-BID reports."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from services.intelligence.tender_brief import links_for_display_identifier
from services.product.bid_report import (
    derive_recommendation,
    recommended_actions,
    render_bid_report_pdf,
)
from services.product.entitlements import EntitlementLimitExceeded, consume_entitlement

from ..auth import get_current_user
from ..db import get_tenant_scoped_conn
from ..workspace import tenant_uuid

router = APIRouter(prefix="/v1/bid-reports", tags=["bids"])


class BidReportResponse(BaseModel):
    process_id: str
    public_id: str
    title: str
    generated_at: datetime
    recommendation: str
    confidence: float
    recommendation_reasons: list[str]
    opportunity_score: float
    data_confidence: float
    buyer_name: str | None = None
    budget: Decimal | None = None
    currency: str = "EUR"
    deadline: datetime | None = None
    geography: list[str] = Field(default_factory=list)
    fit: dict[str, float] = Field(default_factory=dict)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    mandatory_requirements: list[dict[str, Any]] = Field(default_factory=list)
    missing_certificates: list[dict[str, Any]] = Field(default_factory=list)
    competitors: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[dict[str, str]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


async def _build_report(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> BidReportResponse:
    core = (
        await conn.execute(
            sa.text(
                """
                WITH representative AS (
                    SELECT
                        act.id,
                        COALESCE(act.amount_gross, act.amount_net) AS amount,
                        act.submission_deadline AS deadline
                    FROM procurement_acts act
                    WHERE act.process_id = CAST(:process_id AS UUID)
                      AND act.is_current = TRUE
                    ORDER BY
                        (act.act_type = 'NOTICE') DESC,
                        COALESCE(
                            act.publication_date,
                            act.submission_date,
                            act.decision_date
                        ) DESC NULLS LAST
                    LIMIT 1
                ),
                profile AS (
                    SELECT classification_version
                    FROM business_profiles
                    WHERE tenant_id = CAST(:tenant_id AS UUID)
                )
                SELECT
                    process.id,
                    process.public_id,
                    process.title,
                    process.currency,
                    buyer.canonical_name AS buyer_name,
                    COALESCE(
                        process.estimated_value,
                        process.current_contract_value,
                        representative.amount
                    ) AS budget,
                    representative.deadline,
                    COALESCE(score.total_score, 50) AS opportunity_score,
                    COALESCE(score.data_confidence_score, 35) AS data_confidence,
                    COALESCE(score.cpv_company_fit_score, 0) AS cpv_fit,
                    COALESCE(score.buyer_affinity_score, 0) AS buyer_fit,
                    COALESCE(score.timing_score, 0) AS timing_fit,
                    COALESCE(score.competitive_attractiveness_score, 0) AS competition_fit,
                    COALESCE(score.contract_value_fit_score, 0) AS value_fit
                FROM procurement_processes process
                LEFT JOIN representative ON TRUE
                LEFT JOIN entities buyer ON buyer.id = process.buyer_entity_id
                LEFT JOIN profile ON TRUE
                LEFT JOIN opportunity_scores score
                  ON score.process_id = process.id
                 AND score.tenant_id = CAST(:tenant_id AS UUID)
                 AND score.profile_version = profile.classification_version
                WHERE process.id = CAST(:process_id AS UUID)
                """
            ),
            {"process_id": str(process_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    if core is None:
        raise HTTPException(status_code=404, detail="procurement process not found")

    requirement_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT
                    requirement.id,
                    requirement.requirement_type,
                    requirement.title,
                    requirement.description,
                    requirement.status,
                    requirement.mandatory,
                    requirement.evidence_document_id,
                    requirement.evidence_page,
                    requirement.source_excerpt,
                    EXISTS (
                        SELECT 1
                        FROM bid_certificate_links link
                        WHERE link.requirement_id = requirement.id
                    ) AS has_certificate
                FROM bid_workspaces workspace
                JOIN bid_requirements requirement
                  ON requirement.bid_workspace_id = workspace.id
                WHERE workspace.tenant_id = CAST(:tenant_id AS UUID)
                  AND workspace.process_id = CAST(:process_id AS UUID)
                ORDER BY requirement.mandatory DESC, requirement.requirement_type, requirement.title
                """
            ),
            {"tenant_id": str(tenant_id), "process_id": str(process_id)},
        )
    ).mappings().all()
    if not requirement_rows:
        requirement_rows = (
            await conn.execute(
                sa.text(
                    """
                    SELECT
                        field.id,
                        CASE
                            WHEN field.category ILIKE '%cert%' THEN 'CERTIFICATE'
                            WHEN field.category ILIKE '%technic%' THEN 'TECHNICAL'
                            WHEN field.category ILIKE '%financ%' THEN 'FINANCIAL'
                            ELSE 'OTHER'
                        END AS requirement_type,
                        field.field_name AS title,
                        field.value::TEXT AS description,
                        'UNREVIEWED' AS status,
                        TRUE AS mandatory,
                        field.document_id AS evidence_document_id,
                        field.page_number AS evidence_page,
                        field.source_excerpt,
                        FALSE AS has_certificate
                    FROM document_compliance_fields field
                    WHERE field.process_id = CAST(:process_id AS UUID)
                    ORDER BY field.confidence DESC, field.category, field.field_name
                    LIMIT 50
                    """
                ),
                {"process_id": str(process_id)},
            )
        ).mappings().all()

    requirements = [dict(row) for row in requirement_rows]
    missing_requirements = [
        requirement
        for requirement in requirements
        if requirement["mandatory"]
        and requirement["status"] in {"MISSING", "PARTIAL", "UNREVIEWED"}
    ]
    hard_blockers = [
        requirement
        for requirement in requirements
        if requirement["mandatory"] and requirement["status"] in {"MISSING", "PARTIAL"}
    ]
    missing_certificates = [
        requirement
        for requirement in requirements
        if requirement["requirement_type"] == "CERTIFICATE"
        and requirement["status"] != "MET"
        and not requirement["has_certificate"]
    ]

    participant_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT
                    COALESCE(entity.canonical_name, participation.participant_name_raw,
                             participation.participant_afm_raw) AS name,
                    participation.participation_role AS role,
                    participation.evidence_type,
                    participation.confidence,
                    participation.source_page,
                    participation.document_id
                FROM process_participations participation
                LEFT JOIN entities entity ON entity.id = participation.entity_id
                WHERE participation.process_id = CAST(:process_id AS UUID)
                ORDER BY
                    (participation.participation_role = 'WINNER') DESC,
                    participation.confidence DESC,
                    name
                """
            ),
            {"process_id": str(process_id)},
        )
    ).mappings().all()
    competitors = [
        {
            **dict(row),
            "confidence": float(row["confidence"]),
            "label": (
                f"{row['name']} · "
                f"{'πιθανός incumbent' if row['role'] == 'WINNER' else 'τεκμηριωμένος συμμετέχων'}"
            ),
        }
        for row in participant_rows
    ]

    evidence_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT DISTINCT ON (document.id)
                    document.id AS document_id,
                    COALESCE(document.title, document.document_type, 'Official document') AS title,
                    document.source_url,
                    source.source_system,
                    source.source_native_id,
                    identifier.scheme AS identifier_scheme,
                    identifier.value_normalized AS source_identifier
                FROM procurement_acts act
                JOIN source_records source ON source.id = act.source_record_id
                LEFT JOIN documents document ON document.act_id = act.id
                LEFT JOIN act_identifiers identifier
                  ON identifier.act_id = act.id
                 AND identifier.scheme IN ('ADAM', 'ADA', 'TED_NOTICE_ID')
                WHERE act.process_id = CAST(:process_id AS UUID)
                  AND (
                      document.id IS NOT NULL
                      OR identifier.value_normalized IS NOT NULL
                  )
                ORDER BY
                    document.id,
                    (identifier.scheme = 'ADAM') DESC,
                    source.fetched_at DESC
                LIMIT 30
                """
            ),
            {"process_id": str(process_id)},
        )
    ).mappings().all()
    evidence: list[dict[str, Any]] = []
    for row in evidence_rows:
        official_url, inferred_document_url = links_for_display_identifier(
            row["identifier_scheme"],
            row["source_identifier"],
        )
        url = row["source_url"] or inferred_document_url or official_url
        evidence.append(
            {
                "document_id": str(row["document_id"]) if row["document_id"] else None,
                "title": row["title"],
                "label": row["source_identifier"] or row["title"],
                "source_system": row["source_system"],
                "source_identifier": row["source_identifier"],
                "url": url,
            }
        )

    location_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT DISTINCT COALESCE(
                    location.municipality_name,
                    location.regional_unit_name,
                    location.region_name,
                    location.place_text,
                    location.nuts_code
                ) AS label
                FROM procurement_acts act
                JOIN act_locations location ON location.act_id = act.id
                WHERE act.process_id = CAST(:process_id AS UUID)
                LIMIT 12
                """
            ),
            {"process_id": str(process_id)},
        )
    ).scalars().all()

    deadline = core["deadline"]
    deadline_passed = deadline is not None and deadline < datetime.now(timezone.utc)
    recommendation, confidence, recommendation_reasons = derive_recommendation(
        opportunity_score=float(core["opportunity_score"]),
        data_confidence=float(core["data_confidence"]),
        mandatory_blockers=len(hard_blockers),
        deadline_passed=deadline_passed,
    )
    fit = {
        "cpv": float(core["cpv_fit"]),
        "buyer": float(core["buyer_fit"]),
        "timing": float(core["timing_fit"]),
        "competition": float(core["competition_fit"]),
        "value": float(core["value_fit"]),
    }
    risks: list[dict[str, Any]] = []
    for key, value in fit.items():
        if value < 45:
            risks.append(
                {
                    "code": f"LOW_{key.upper()}_FIT",
                    "severity": "WARNING",
                    "label": f"Χαμηλή επίδοση {key}: {value:.0f}/100",
                }
            )
    risks.extend(
        {
            "code": "MANDATORY_REQUIREMENT",
            "severity": "ERROR" if requirement["status"] == "MISSING" else "WARNING",
            "label": requirement["title"],
            "evidence_document_id": (
                str(requirement["evidence_document_id"])
                if requirement["evidence_document_id"]
                else None
            ),
            "evidence_page": requirement["evidence_page"],
        }
        for requirement in missing_requirements
    )
    if not evidence:
        risks.append(
            {
                "code": "NO_OFFICIAL_EVIDENCE",
                "severity": "ERROR",
                "label": "Δεν υπάρχουν διαθέσιμα επίσημα έγγραφα ή identifiers.",
            }
        )

    serial_requirements = [
        {
            **requirement,
            "id": str(requirement["id"]),
            "evidence_document_id": (
                str(requirement["evidence_document_id"])
                if requirement["evidence_document_id"]
                else None
            ),
        }
        for requirement in requirements
        if requirement["mandatory"]
    ]
    serial_missing_certificates = [
        {
            **requirement,
            "id": str(requirement["id"]),
            "evidence_document_id": (
                str(requirement["evidence_document_id"])
                if requirement["evidence_document_id"]
                else None
            ),
        }
        for requirement in missing_certificates
    ]
    next_actions = recommended_actions(
        recommendation=recommendation,
        missing_requirements=missing_requirements,
        missing_certificates=missing_certificates,
        deadline=deadline,
    )
    limitations = [
        "Η σύσταση είναι συμβουλευτική και δεν αποτελεί πρόβλεψη νίκης.",
        "Οι απαιτήσεις πρέπει να επιβεβαιώνονται στο ισχύον επίσημο τεύχος.",
    ]
    if float(core["data_confidence"]) < 70:
        limitations.append("Η χαμηλή κάλυψη δεδομένων μειώνει τη βεβαιότητα της σύστασης.")

    return BidReportResponse(
        process_id=str(process_id),
        public_id=core["public_id"],
        title=core["title"] or core["public_id"],
        generated_at=datetime.now(timezone.utc),
        recommendation=recommendation,
        confidence=confidence,
        recommendation_reasons=recommendation_reasons,
        opportunity_score=float(core["opportunity_score"]),
        data_confidence=float(core["data_confidence"]),
        buyer_name=core["buyer_name"],
        budget=core["budget"],
        currency=core["currency"] or "EUR",
        deadline=deadline,
        geography=[label for label in location_rows if label],
        fit=fit,
        risks=risks,
        mandatory_requirements=serial_requirements,
        missing_certificates=serial_missing_certificates,
        competitors=competitors,
        evidence=evidence,
        next_actions=next_actions,
        limitations=limitations,
    )


@router.get("/{process_id}", response_model=BidReportResponse)
async def get_bid_report(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> BidReportResponse:
    try:
        await consume_entitlement(
            conn,
            tenant_id=tenant_uuid(user),
            metric_code="ai_reports_month",
        )
    except EntitlementLimitExceeded as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "ENTITLEMENT_LIMIT",
                "metric": exc.metric_code,
                "limit": exc.limit,
                "usage": exc.usage,
            },
        ) from exc
    return await _build_report(
        conn,
        process_id=process_id,
        tenant_id=tenant_uuid(user),
    )


@router.get("/{process_id}/pdf")
async def download_bid_report(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    report = await _build_report(
        conn,
        process_id=process_id,
        tenant_id=tenant_uuid(user),
    )
    pdf = render_bid_report_pdf(report.model_dump(mode="json"))
    safe_public_id = re.sub(r"[^A-Za-z0-9_-]+", "-", report.public_id).strip("-")
    filename = f"procintel-bid-report-{safe_public_id or process_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

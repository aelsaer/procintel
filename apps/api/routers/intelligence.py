"""Product-facing intelligence APIs over canonical data and SQL marts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    act_cpv_codes,
    act_locations,
    act_parties,
    business_profiles,
    entities,
    entity_company_snapshots,
    entity_identifiers,
    funding_links,
    funding_projects,
    opportunity_pipeline_items,
    opportunity_scores,
    procurement_acts,
    procurement_processes,
)

from services.analytics.risk_indicators import compute_risk_indicators
from services.intelligence.tender_brief import links_for_display_identifier
from services.search_index.lexical import (
    normalized_text_sql,
    query_concept_pattern,
    query_prefilter,
    query_token_patterns,
)

from ..auth import get_current_user
from ..db import get_conn, get_tenant_scoped_conn
from ..workspace import tenant_uuid

router = APIRouter(prefix="/v1/intelligence", tags=["intelligence"])
_OPPORTUNITY_TITLE_SEARCH_SQL = normalized_text_sql("pp.title")


class MarketMetricResponse(BaseModel):
    cpv_prefix: str
    nuts_code: str | None
    period_year: int
    procedure_type: str | None
    contract_count: int
    total_value: Decimal | None
    average_value: Decimal | None
    median_value: Decimal | None
    supplier_count: int
    buyer_count: int
    hhi: Decimal | None
    value_basis: str = "current_contract_value_net"
    refreshed_at: Any | None = None


class OpportunityIntelligenceResponse(BaseModel):
    process_id: str
    title: str | None
    buyer_id: str | None
    buyer_name: str | None
    amount: Decimal | None
    deadline: datetime | None
    adam: str | None
    official_url: str | None
    document_url: str | None
    cpv_codes: list[str]
    locations: list[str]
    score: Decimal | None
    score_breakdown: dict[str, Decimal | None]
    evidence: list[dict[str, Any]]
    pipeline_stage: str | None


class RelationshipNode(BaseModel):
    id: str
    node_type: str
    label: str
    value: Decimal | None = None


class RelationshipEdge(BaseModel):
    source: str
    target: str
    relation_type: str
    value: Decimal | None = None
    confidence: float
    evidence: dict[str, Any]


class RelationshipResponse(BaseModel):
    nodes: list[RelationshipNode]
    edges: list[RelationshipEdge]
    table: list[dict[str, Any]]


class AssistantRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    cpv_prefix: str | None = None
    cpv_prefixes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    taxonomy_match: str | None = None
    nuts_code: str | None = None
    municipality: str | None = None
    amount_min: Decimal | None = Field(default=None, ge=0)
    date_from: date | None = None
    date_to: date | None = None


class AssistantResponse(BaseModel):
    answer: str
    intent: str
    data: list[dict[str, Any]]
    visualization: dict[str, Any]
    methodology: str


class RiskIndicatorResponse(BaseModel):
    indicator_type: str
    message: str
    subject: dict[str, Any]
    value: Any
    benchmark: Any
    minimum_sample: int
    sample_size: int
    confidence: str
    sources: list[str]
    calculated_at: str
    limitations: str
    definition: str


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {label}") from exc


def _mart_cpv_likes(cpv_prefix: str | None, cpv_prefixes: str | None) -> list[str]:
    values = [
        value.strip().split("-", 1)[0]
        for value in [cpv_prefix or "", *(cpv_prefixes or "").split(",")]
        if value.strip()
    ]
    return [f"{value[:4]}%" for value in dict.fromkeys(values)]


def _scope_cpv_likes(cpv_prefix: str | None, cpv_prefixes: str | None) -> list[str]:
    values = [
        value.strip().split("-", 1)[0][:8]
        for value in [cpv_prefix or "", *(cpv_prefixes or "").split(",")]
        if value.strip()
    ]
    return [f"{value}%" for value in dict.fromkeys(values)]


def _scope_keyword_likes(keyword: str | None, keywords: str | None) -> list[str]:
    values = [
        value.strip()
        for value in [keyword or "", *(keywords or "").split(",")]
        if value.strip()
    ]
    return [f"%{value}%" for value in dict.fromkeys(values)]


@router.get("/markets", response_model=list[MarketMetricResponse])
async def market_metrics(
    cpv_prefix: str | None = None, cpv_prefixes: str | None = None,
    nuts_code: str | None = None,
    period_year: int | None = Query(default=None, ge=2000, le=2100),
    procedure_type: str | None = None, limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_conn),
) -> list[MarketMetricResponse]:
    rows = (await conn.execute(sa.text(
        """
        SELECT m.cpv_prefix_4, m.nuts_code, m.period_year, m.procedure_type,
               m.contract_count, m.total_value_net, m.avg_value_net, m.median_value_net,
               m.supplier_count, m.buyer_count, h.hhi, rs.last_refresh_finished_at
        FROM market_value_metrics m
        LEFT JOIN market_hhi h ON h.cpv_prefix_4 = m.cpv_prefix_4
            AND COALESCE(h.nuts_code, '') = COALESCE(m.nuts_code, '') AND h.period_year = m.period_year
        LEFT JOIN mart_refresh_state rs ON rs.mart_name = 'market_value_metrics'
        WHERE (
            CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
            OR m.cpv_prefix_4 LIKE ANY(CAST(:cpv_likes AS TEXT[]))
        )
          AND (CAST(:nuts AS text) IS NULL OR m.nuts_code LIKE CAST(:nuts AS text) || '%')
          AND (CAST(:year AS integer) IS NULL OR m.period_year = CAST(:year AS integer))
          AND (CAST(:procedure AS text) IS NULL OR m.procedure_type = CAST(:procedure AS text))
        ORDER BY m.total_value_net DESC NULLS LAST LIMIT :limit
        """
    ), {
        "cpv_likes": _mart_cpv_likes(cpv_prefix, cpv_prefixes),
        "nuts": nuts_code, "year": period_year, "procedure": procedure_type, "limit": limit,
    })).all()
    return [MarketMetricResponse(
        cpv_prefix=row.cpv_prefix_4, nuts_code=row.nuts_code, period_year=row.period_year,
        procedure_type=row.procedure_type, contract_count=row.contract_count,
        total_value=row.total_value_net, average_value=row.avg_value_net,
        median_value=row.median_value_net, supplier_count=row.supplier_count,
        buyer_count=row.buyer_count, hhi=row.hhi, refreshed_at=row.last_refresh_finished_at,
    ) for row in rows]


@router.get("/opportunities", response_model=list[OpportunityIntelligenceResponse])
async def tenant_opportunities(
    process_id: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[OpportunityIntelligenceResponse]:
    tenant_id = tenant_uuid(user)
    token_patterns = query_token_patterns(q or "")
    lexical_prefilter = query_prefilter(q or "")
    rows = (await conn.execute(sa.text(
        f"""
        SELECT pp.id AS process_id, pp.title, pp.buyer_entity_id, buyer.canonical_name AS buyer_name,
               COALESCE(pp.estimated_value, MAX(a.amount_gross)) AS amount,
               MAX(a.submission_deadline) FILTER (WHERE a.act_type IN ('REQUEST','NOTICE')) AS deadline,
               MAX(identifier.value_normalized) FILTER (WHERE identifier.scheme = 'ADAM') AS adam,
               ARRAY_REMOVE(ARRAY_AGG(DISTINCT cpv.cpv_code), NULL) AS cpv_codes,
               ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(loc.municipality_name, loc.place_text, loc.region_name)), NULL) AS locations,
               score.total_score, score.cpv_company_fit_score, score.buyer_affinity_score,
               score.timing_score, score.competitive_attractiveness_score,
               score.contract_value_fit_score, score.data_confidence_score, score.evidence,
               pipe.stage AS pipeline_stage
        FROM procurement_processes pp
        JOIN procurement_acts a ON a.process_id = pp.id AND a.is_current = TRUE
        LEFT JOIN entities buyer ON buyer.id = pp.buyer_entity_id
        LEFT JOIN act_cpv_codes cpv ON cpv.act_id = a.id
        LEFT JOIN act_locations loc ON loc.act_id = a.id
        LEFT JOIN act_identifiers identifier ON identifier.act_id = a.id
        LEFT JOIN opportunity_scores score ON score.process_id = pp.id AND score.tenant_id = CAST(:tenant_id AS uuid)
        LEFT JOIN opportunity_pipeline_items pipe ON pipe.process_id = pp.id AND pipe.tenant_id = CAST(:tenant_id AS uuid)
        WHERE a.act_type IN ('REQUEST', 'NOTICE')
          AND (CAST(:process_id AS uuid) IS NULL OR pp.id = CAST(:process_id AS uuid))
          AND score.id IS NOT NULL
          AND (
              NOT CAST(:has_lexical_query AS BOOLEAN)
              OR (
                  {_OPPORTUNITY_TITLE_SEARCH_SQL} ILIKE :lexical_prefilter
                  AND {_OPPORTUNITY_TITLE_SEARCH_SQL} ~* ALL(CAST(:token_patterns AS TEXT[]))
              )
          )
        GROUP BY pp.id, pp.title, pp.buyer_entity_id, buyer.canonical_name,
                 score.total_score, score.cpv_company_fit_score, score.buyer_affinity_score,
                 score.timing_score, score.competitive_attractiveness_score,
                 score.contract_value_fit_score, score.data_confidence_score, score.evidence, pipe.stage
        ORDER BY score.total_score DESC NULLS LAST, MAX(COALESCE(a.publication_date, a.submission_date, a.decision_date)) DESC NULLS LAST
        LIMIT :limit
        """
    ), {
        "tenant_id": str(tenant_id),
        "process_id": process_id,
        "has_lexical_query": bool(token_patterns),
        "lexical_prefilter": lexical_prefilter,
        "token_patterns": token_patterns,
        "limit": limit,
    })).all()
    results = []
    for row in rows:
        official_url, document_url = links_for_display_identifier("ADAM", row.adam)
        results.append(OpportunityIntelligenceResponse(
            process_id=str(row.process_id), title=row.title,
            buyer_id=str(row.buyer_entity_id) if row.buyer_entity_id else None, buyer_name=row.buyer_name,
            amount=row.amount, deadline=row.deadline, adam=row.adam,
            official_url=official_url, document_url=document_url,
            cpv_codes=row.cpv_codes or [], locations=row.locations or [],
            score=row.total_score,
            score_breakdown={
                "cpv_fit": row.cpv_company_fit_score, "buyer_affinity": row.buyer_affinity_score,
                "timing": row.timing_score, "competition": row.competitive_attractiveness_score,
                "value_fit": row.contract_value_fit_score, "data_confidence": row.data_confidence_score,
            }, evidence=row.evidence or [], pipeline_stage=row.pipeline_stage,
        ))
    return results


@router.get("/market-dashboard", response_model=dict[str, Any])
async def market_dashboard(
    cpv_prefix: str | None = None,
    cpv_prefixes: str | None = None,
    period_year: int | None = Query(default=None, ge=2000, le=2100),
    conn: AsyncConnection = Depends(get_conn),
) -> dict[str, Any]:
    params = {
        "cpv_likes": _mart_cpv_likes(cpv_prefix, cpv_prefixes),
        "year": period_year,
    }
    summary = (await conn.execute(sa.text(
        """
        WITH filtered_contracts AS (
            SELECT DISTINCT contract.id, contract.amount_net
            FROM procurement_acts contract
            WHERE contract.act_type = 'CONTRACT'
              AND contract.is_current = TRUE
              AND (
                  CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
                  OR EXISTS (
                      SELECT 1 FROM act_cpv_codes cpv
                      WHERE cpv.act_id = contract.id
                        AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                  )
              )
              AND (
                  CAST(:year AS INTEGER) IS NULL
                  OR EXTRACT(YEAR FROM COALESCE(
                      contract.decision_date, contract.publication_date,
                      contract.submission_date, contract.start_date
                  )) = CAST(:year AS INTEGER)
              )
        )
        SELECT COUNT(*) AS contract_count,
               COALESCE(SUM(amount_net), 0) AS total_value,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount_net)
                   FILTER (WHERE amount_net IS NOT NULL) AS median_contract_value,
               (
                   SELECT COUNT(DISTINCT party.entity_id)
                   FROM filtered_contracts contract
                   JOIN act_parties party ON party.act_id = contract.id
                   WHERE party.party_role IN ('SUPPLIER', 'CONTRACTOR')
               ) AS supplier_observations,
               (
                   SELECT COUNT(DISTINCT party.entity_id)
                   FROM filtered_contracts contract
                   JOIN act_parties party ON party.act_id = contract.id
                   WHERE party.party_role IN ('BUYER', 'CONTRACTING_AUTHORITY')
               ) AS buyer_observations
        FROM filtered_contracts
        """
    ), params)).mappings().one()
    concentration = (await conn.execute(sa.text(
        """
        SELECT AVG(hhi) AS average_hhi, MAX(hhi) AS maximum_hhi,
               SUM(supplier_count) AS supplier_observations
        FROM market_hhi
        WHERE (
              CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
              OR cpv_prefix_4 LIKE ANY(CAST(:cpv_likes AS TEXT[]))
          )
          AND (CAST(:year AS integer) IS NULL OR period_year=CAST(:year AS integer))
        """
    ), params)).mappings().one()
    modifications = (await conn.execute(sa.text(
        """
        SELECT COUNT(*) AS contracts_observed,
               COUNT(*) FILTER (WHERE amendment_count > 0) AS modified_contracts,
               COUNT(*) FILTER (WHERE amendment_count > 0)::numeric / NULLIF(COUNT(*),0) AS modification_rate,
               AVG(value_uplift_ratio) FILTER (WHERE amendment_count > 0) AS average_value_uplift
        FROM contract_modification_stats stats
        JOIN procurement_acts contract ON contract.id = stats.contract_act_id
        WHERE contract.is_current = TRUE
          AND (
              CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
              OR EXISTS (
                  SELECT 1 FROM act_cpv_codes cpv
                  WHERE cpv.act_id = contract.id
                    AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
              )
          )
          AND (
              CAST(:year AS INTEGER) IS NULL
              OR EXTRACT(YEAR FROM COALESCE(
                  contract.decision_date, contract.publication_date,
                  contract.submission_date, contract.start_date
              )) = CAST(:year AS INTEGER)
          )
        """
    ), params)).mappings().one()
    cycle = (await conn.execute(sa.text(
        """
        SELECT AVG(request_to_notice_days) AS request_to_notice_days,
               AVG(notice_to_award_days) AS notice_to_award_days,
               AVG(award_to_contract_days) AS award_to_contract_days,
               AVG(contract_to_first_payment_days) AS contract_to_first_payment_days,
               COUNT(*) AS processes_observed
        FROM cycle_time_metrics cycle
        WHERE (
              CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
              OR EXISTS (
                  SELECT 1
                  FROM procurement_acts act
                  JOIN act_cpv_codes cpv ON cpv.act_id = act.id
                  WHERE act.process_id = cycle.process_id
                    AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
              )
          )
          AND (
              CAST(:year AS INTEGER) IS NULL
              OR EXISTS (
                  SELECT 1 FROM procurement_acts act
                  WHERE act.process_id = cycle.process_id
                    AND EXTRACT(YEAR FROM COALESCE(
                        act.decision_date, act.publication_date,
                        act.submission_date, act.start_date
                    )) = CAST(:year AS INTEGER)
              )
          )
        """
    ), params)).mappings().one()
    payments = (await conn.execute(sa.text(
        """
        SELECT AVG(payment_execution_ratio) AS average_execution_ratio,
               COUNT(*) AS contracts_observed,
               COUNT(*) FILTER (WHERE coverage_badge='HIGH_COVERAGE') AS high_coverage,
               COUNT(*) FILTER (WHERE coverage_badge='PARTIAL_COVERAGE') AS partial_coverage,
               COUNT(*) FILTER (WHERE coverage_badge='UNKNOWN') AS unknown_coverage
        FROM payment_execution execution
        JOIN procurement_acts contract ON contract.id = execution.contract_act_id
        WHERE contract.is_current = TRUE
          AND (
              CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
              OR EXISTS (
                  SELECT 1 FROM act_cpv_codes cpv
                  WHERE cpv.act_id = contract.id
                    AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
              )
          )
          AND (
              CAST(:year AS INTEGER) IS NULL
              OR EXTRACT(YEAR FROM COALESCE(
                  contract.decision_date, contract.publication_date,
                  contract.submission_date, contract.start_date
              )) = CAST(:year AS INTEGER)
          )
        """
    ), params)).mappings().one()
    signals = (await conn.execute(sa.text(
        """
        SELECT (
                 SELECT COUNT(*) FROM incumbent_signals incumbent
                 JOIN procurement_acts contract ON contract.id = incumbent.most_recent_active_contract_act_id
                 WHERE (
                     CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
                     OR incumbent.cpv_prefix_4 LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                 )
                   AND (
                     CAST(:year AS INTEGER) IS NULL
                     OR EXTRACT(YEAR FROM COALESCE(
                         contract.decision_date, contract.publication_date,
                         contract.submission_date, contract.start_date
                     )) = CAST(:year AS INTEGER)
                   )
               ) AS incumbents,
               (
                 SELECT COUNT(*) FROM renewal_signals renewal
                 JOIN procurement_acts contract ON contract.id = renewal.contract_act_id
                 WHERE renewal.renewal_watch_active
                   AND (
                     CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
                     OR EXISTS (
                         SELECT 1 FROM act_cpv_codes cpv
                         WHERE cpv.act_id = contract.id
                           AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                     )
                   )
               ) AS upcoming_renewals,
               (
                 SELECT COUNT(DISTINCT project.id)
                 FROM funding_projects project
                 LEFT JOIN funding_links link ON link.funding_project_id = project.id
                 LEFT JOIN act_cpv_codes cpv ON cpv.act_id = link.act_id
                 WHERE (
                     CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
                     OR LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                 )
               ) AS funding_projects,
               (
                 SELECT COALESCE(SUM(project.budget), 0)
                 FROM funding_projects project
                 WHERE (
                     CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
                     OR EXISTS (
                         SELECT 1 FROM funding_links link
                         JOIN act_cpv_codes cpv ON cpv.act_id = link.act_id
                         WHERE link.funding_project_id = project.id
                           AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                     )
                 )
               ) AS funding_budget,
               (
                 SELECT COALESCE(SUM(project.paid_amount), 0)
                 FROM funding_projects project
                 WHERE (
                     CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
                     OR EXISTS (
                         SELECT 1 FROM funding_links link
                         JOIN act_cpv_codes cpv ON cpv.act_id = link.act_id
                         WHERE link.funding_project_id = project.id
                           AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                     )
                 )
               ) AS funding_paid
        """
    ), params)).mappings().one()
    procedures = (await conn.execute(sa.text(
        """
        WITH filtered_contracts AS (
            SELECT DISTINCT contract.id, contract.procedure_type, contract.amount_net
            FROM procurement_acts contract
            WHERE contract.act_type = 'CONTRACT'
              AND contract.is_current = TRUE
              AND (
                  CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
                  OR EXISTS (
                      SELECT 1 FROM act_cpv_codes cpv
                      WHERE cpv.act_id = contract.id
                        AND LEFT(cpv.cpv_code, 4) LIKE ANY(CAST(:cpv_likes AS TEXT[]))
                  )
              )
              AND (
                  CAST(:year AS INTEGER) IS NULL
                  OR EXTRACT(YEAR FROM COALESCE(
                      contract.decision_date, contract.publication_date,
                      contract.submission_date, contract.start_date
                  )) = CAST(:year AS INTEGER)
              )
        )
        SELECT COALESCE(procedure_type, 'UNKNOWN') AS procedure_type,
               COUNT(*) AS contract_count,
               COALESCE(SUM(amount_net), 0) AS total_value
        FROM filtered_contracts
        GROUP BY procedure_type
        ORDER BY total_value DESC NULLS LAST
        LIMIT 12
        """
    ), params)).mappings().all()
    supplier_trends = (await conn.execute(sa.text(
        """
        SELECT sms.period_year, e.id AS supplier_id, e.canonical_name AS supplier_name,
               SUM(sms.supplier_value) AS recorded_value,
               SUM(sms.supplier_contract_count) AS contracts
        FROM supplier_market_share sms JOIN entities e ON e.id=sms.supplier_entity_id
        WHERE (
              CARDINALITY(CAST(:cpv_likes AS TEXT[])) = 0
              OR sms.cpv_prefix_4 LIKE ANY(CAST(:cpv_likes AS TEXT[]))
          )
          AND (CAST(:year AS integer) IS NULL OR sms.period_year=CAST(:year AS integer))
        GROUP BY sms.period_year,e.id,e.canonical_name
        ORDER BY sms.period_year DESC, recorded_value DESC NULLS LAST LIMIT 25
        """
    ), params)).mappings().all()
    return {
        "summary": dict(summary), "concentration": dict(concentration),
        "modifications": dict(modifications), "cycle_time": dict(cycle),
        "payment_execution": dict(payments), "signals": dict(signals),
        "procedure_mix": [dict(row) for row in procedures],
        "supplier_trends": [dict(row) for row in supplier_trends],
        "methodologies": ["market_value", "hhi", "modification", "cycle_time", "payment_execution", "renewal"],
    }


@router.get("/buyers/{buyer_id}", response_model=dict[str, Any])
async def buyer_intelligence(buyer_id: str, conn: AsyncConnection = Depends(get_conn)) -> dict[str, Any]:
    target = _uuid(buyer_id, "buyer_id")
    identity = (await conn.execute(sa.text(
        """
        SELECT e.id, e.canonical_name,
               MAX(i.value_normalized) FILTER (WHERE i.scheme='AFM' AND i.is_current) AS afm,
               COUNT(DISTINCT a.id) FILTER (WHERE a.act_type='CONTRACT') AS contract_count,
               COALESCE(SUM(a.amount_net) FILTER (WHERE a.act_type='CONTRACT'), 0) AS total_value
        FROM entities e
        LEFT JOIN entity_identifiers i ON i.entity_id=e.id
        LEFT JOIN act_parties p ON p.entity_id=e.id AND p.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
        LEFT JOIN procurement_acts a ON a.id=p.act_id AND a.is_current=TRUE
        WHERE e.id=CAST(:id AS uuid) GROUP BY e.id, e.canonical_name
        """
    ), {"id": str(target)})).first()
    if identity is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    suppliers = (await conn.execute(sa.text(
        """
        SELECT s.id, s.canonical_name, SUM(sp.amount) AS value, COUNT(DISTINCT sp.act_id) AS contracts
        FROM act_parties bp JOIN act_parties sp ON sp.act_id=bp.act_id AND sp.party_role IN ('SUPPLIER','CONTRACTOR')
        JOIN entities s ON s.id=sp.entity_id
        WHERE bp.entity_id=CAST(:id AS uuid) AND bp.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
        GROUP BY s.id, s.canonical_name ORDER BY value DESC NULLS LAST LIMIT 10
        """
    ), {"id": str(target)})).mappings().all()
    cpv = (await conn.execute(sa.text(
        """
        SELECT LEFT(c.cpv_code,4) AS cpv_prefix, COUNT(DISTINCT c.act_id) AS acts, SUM(a.amount_net) AS value
        FROM act_parties p JOIN procurement_acts a ON a.id=p.act_id
        JOIN act_cpv_codes c ON c.act_id=a.id
        WHERE p.entity_id=CAST(:id AS uuid) AND p.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
        GROUP BY LEFT(c.cpv_code,4) ORDER BY value DESC NULLS LAST LIMIT 10
        """
    ), {"id": str(target)})).mappings().all()
    concentration = (await conn.execute(sa.text("SELECT * FROM buyer_concentration WHERE buyer_entity_id=CAST(:id AS uuid)"), {"id": str(target)})).mappings().first()
    renewals = (await conn.execute(sa.text(
        """
        SELECT r.contract_act_id, r.process_id, r.end_date, r.days_to_end, r.avg_lead_time_days, r.renewal_watch_active
        FROM renewal_signals r JOIN act_parties p ON p.act_id=r.contract_act_id
        WHERE p.entity_id=CAST(:id AS uuid) AND p.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
        ORDER BY r.end_date LIMIT 20
        """
    ), {"id": str(target)})).mappings().all()
    return {"identity": dict(identity._mapping), "top_suppliers": [dict(row) for row in suppliers], "cpv_mix": [dict(row) for row in cpv], "concentration": dict(concentration) if concentration else None, "renewals": [dict(row) for row in renewals], "methodology": ["buyer_concentration", "renewal"]}


@router.get("/suppliers/{supplier_id}", response_model=dict[str, Any])
async def supplier_intelligence(supplier_id: str, conn: AsyncConnection = Depends(get_conn)) -> dict[str, Any]:
    target = _uuid(supplier_id, "supplier_id")
    identity = (await conn.execute(sa.text(
        """
        SELECT e.id, e.canonical_name,
               MAX(i.value_normalized) FILTER (WHERE i.scheme='AFM' AND i.is_current) AS afm,
               MAX(s.gemi_number) FILTER (WHERE s.is_current) AS gemi_number,
               MAX(s.legal_form) FILTER (WHERE s.is_current) AS legal_form,
               MAX(s.company_status) FILTER (WHERE s.is_current) AS company_status,
               COUNT(DISTINCT p.act_id) AS contract_count, COALESCE(SUM(p.amount),0) AS total_value
        FROM entities e LEFT JOIN entity_identifiers i ON i.entity_id=e.id
        LEFT JOIN entity_company_snapshots s ON s.entity_id=e.id
        LEFT JOIN act_parties p ON p.entity_id=e.id AND p.party_role IN ('SUPPLIER','CONTRACTOR')
        WHERE e.id=CAST(:id AS uuid) GROUP BY e.id, e.canonical_name
        """
    ), {"id": str(target)})).first()
    if identity is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    dependencies = (await conn.execute(sa.text(
        """
        SELECT d.*, b.canonical_name AS buyer_name FROM supplier_dependency d
        JOIN entities b ON b.id=d.buyer_entity_id WHERE d.supplier_entity_id=CAST(:id AS uuid)
        ORDER BY d.dependency_ratio DESC NULLS LAST LIMIT 15
        """
    ), {"id": str(target)})).mappings().all()
    activity = (await conn.execute(sa.text(
        """
        SELECT LEFT(c.cpv_code,4) AS cpv_prefix, COALESCE(l.region_name,l.nuts_code) AS geography,
               COUNT(DISTINCT a.id) AS contracts, SUM(p.amount) AS value
        FROM act_parties p JOIN procurement_acts a ON a.id=p.act_id
        LEFT JOIN act_cpv_codes c ON c.act_id=a.id AND c.is_primary=TRUE
        LEFT JOIN act_locations l ON l.act_id=a.id
        WHERE p.entity_id=CAST(:id AS uuid) AND p.party_role IN ('SUPPLIER','CONTRACTOR')
        GROUP BY LEFT(c.cpv_code,4), COALESCE(l.region_name,l.nuts_code)
        ORDER BY value DESC NULLS LAST LIMIT 25
        """
    ), {"id": str(target)})).mappings().all()
    amendments = (await conn.execute(sa.text(
        """
        SELECT m.* FROM contract_modification_stats m JOIN act_parties p ON p.act_id=m.contract_act_id
        WHERE p.entity_id=CAST(:id AS uuid) AND p.party_role IN ('SUPPLIER','CONTRACTOR')
        ORDER BY m.value_uplift_ratio DESC NULLS LAST LIMIT 20
        """
    ), {"id": str(target)})).mappings().all()
    return {"identity": dict(identity._mapping), "buyer_dependency": [dict(row) for row in dependencies], "market_activity": [dict(row) for row in activity], "amendments": [dict(row) for row in amendments], "methodology": ["supplier_dependency", "modification"]}


@router.get("/renewals", response_model=list[dict[str, Any]])
async def renewal_watch(
    active_only: bool = True, limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_conn),
) -> list[dict[str, Any]]:
    rows = (await conn.execute(sa.text(
        """
        SELECT r.*, a.title, buyer.canonical_name AS buyer_name, supplier.canonical_name AS supplier_name
        FROM renewal_signals r JOIN procurement_acts a ON a.id=r.contract_act_id
        LEFT JOIN act_parties bp ON bp.act_id=a.id AND bp.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
        LEFT JOIN entities buyer ON buyer.id=bp.entity_id
        LEFT JOIN act_parties sp ON sp.act_id=a.id AND sp.party_role IN ('SUPPLIER','CONTRACTOR')
        LEFT JOIN entities supplier ON supplier.id=sp.entity_id
        WHERE (NOT CAST(:active_only AS boolean) OR r.renewal_watch_active)
        ORDER BY r.end_date LIMIT :limit
        """
    ), {"active_only": active_only, "limit": limit})).mappings().all()
    return [dict(row) for row in rows]


@router.get("/risk-indicators", response_model=list[RiskIndicatorResponse])
async def risk_indicators(
    limit_per_indicator: int = Query(default=25, ge=1, le=100),
    conn: AsyncConnection = Depends(get_conn),
) -> list[RiskIndicatorResponse]:
    instances = await compute_risk_indicators(conn, limit_per_indicator=limit_per_indicator)
    return [
        RiskIndicatorResponse(
            indicator_type=instance.indicator_type,
            message=instance.message,
            subject=instance.subject,
            value=instance.value,
            benchmark=instance.benchmark,
            minimum_sample=instance.minimum_sample,
            sample_size=instance.sample_size,
            confidence=instance.confidence,
            sources=instance.sources,
            calculated_at=instance.calculated_at.isoformat(),
            limitations=instance.limitations,
            definition=instance.definition,
        )
        for instance in instances
    ]


@router.get("/funding", response_model=list[dict[str, Any]])
async def funding_intelligence(
    limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_conn),
) -> list[dict[str, Any]]:
    rows = (await conn.execute(
        sa.select(
            funding_projects.c.id, funding_projects.c.mis_ops_code, funding_projects.c.title,
            funding_projects.c.program_period, funding_projects.c.budget,
            funding_projects.c.contracted_amount, funding_projects.c.paid_amount,
            funding_projects.c.status, sa.func.count(sa.distinct(funding_links.c.act_id)).label("linked_acts"),
        ).outerjoin(funding_links, funding_links.c.funding_project_id == funding_projects.c.id)
        .group_by(funding_projects.c.id).order_by(funding_projects.c.budget.desc().nulls_last()).limit(limit)
    )).mappings().all()
    return [dict(row) for row in rows]


@router.get("/relationships", response_model=RelationshipResponse)
async def relationship_explorer(
    entity_id: str | None = None, cpv_prefix: str | None = None,
    cpv_prefixes: str | None = None, keyword: str | None = None,
    keywords: str | None = None,
    taxonomy_match: str | None = Query(default=None, max_length=32),
    minimum_value: Decimal | None = Query(default=None, ge=0),
    date_from: date | None = None, date_to: date | None = None,
    nuts_code: str | None = None, municipality: str | None = None,
    relation_type: str | None = None,
    source: str | None = None,
    minimum_confidence: float = Query(default=0.0, ge=0, le=1),
    limit: int = Query(default=250, ge=1, le=1000),
    conn: AsyncConnection = Depends(get_conn),
) -> RelationshipResponse:
    cpv_likes = _scope_cpv_likes(cpv_prefix, cpv_prefixes)
    keyword_values = [
        value.strip()
        for value in [keyword or "", *(keywords or "").split(",")]
        if value.strip()
    ]
    keyword_patterns = [
        pattern
        for value in dict.fromkeys(keyword_values)
        if (pattern := query_concept_pattern(value)) is not None
    ]
    taxonomy_match_mode = str(taxonomy_match or "ANY").upper()
    if taxonomy_match_mode == "KEYWORD_REQUIRED" and keyword_patterns:
        cpv_likes = []
    taxonomy_match_all = taxonomy_match_mode in {"ALL", "CPV_AND_KEYWORD"}
    rows = (await conn.execute(sa.text(
        """
        SELECT pp.id AS process_id, pp.title AS process_title,
               buyer.id AS buyer_id, buyer.canonical_name AS buyer_name,
               supplier.id AS supplier_id, supplier.canonical_name AS supplier_name,
               MAX(COALESCE(sp.amount,a.amount_net)) AS value,
               ARRAY_REMOVE(ARRAY_AGG(DISTINCT cpv.cpv_code),NULL) AS cpv_codes
        FROM procurement_processes pp JOIN procurement_acts a ON a.process_id=pp.id AND a.is_current=TRUE
        LEFT JOIN act_parties bp ON bp.act_id=a.id AND bp.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
        LEFT JOIN entities buyer ON buyer.id=COALESCE(bp.entity_id,pp.buyer_entity_id)
        LEFT JOIN act_parties sp ON sp.act_id=a.id AND sp.party_role IN ('SUPPLIER','CONTRACTOR')
        LEFT JOIN entities supplier ON supplier.id=sp.entity_id
        LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id
        LEFT JOIN act_locations loc ON loc.act_id=a.id
        WHERE (CAST(:entity_id AS uuid) IS NULL OR buyer.id=CAST(:entity_id AS uuid) OR supplier.id=CAST(:entity_id AS uuid))
          AND (CAST(:date_from AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) >= CAST(:date_from AS date))
          AND (CAST(:date_to AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) <= CAST(:date_to AS date))
          AND (CAST(:nuts AS text) IS NULL OR loc.nuts_code LIKE CAST(:nuts AS text) || '%')
          AND (
              CAST(:municipality_like AS text) IS NULL
              OR loc.municipality_name ILIKE CAST(:municipality_like AS text)
              OR loc.place_text ILIKE CAST(:municipality_like AS text)
              OR loc.regional_unit_name ILIKE CAST(:municipality_like AS text)
          )
          AND procintel_taxonomy_match(
              a.id,
              a.title,
              CAST(:cpv_likes AS TEXT[]),
              CAST(:keyword_patterns AS TEXT[]),
              CAST(:taxonomy_match_all AS BOOLEAN)
          )
        GROUP BY pp.id, pp.title, buyer.id, buyer.canonical_name, supplier.id, supplier.canonical_name
        HAVING (CAST(:minimum_value AS numeric) IS NULL OR MAX(COALESCE(sp.amount,a.amount_net)) >= CAST(:minimum_value AS numeric))
        ORDER BY value DESC NULLS LAST LIMIT :limit
        """
    ), {
        "entity_id": entity_id,
        "minimum_value": minimum_value,
        "cpv_likes": cpv_likes,
        "keyword_patterns": keyword_patterns,
        "taxonomy_match_all": taxonomy_match_all,
        "date_from": date_from,
        "date_to": date_to,
        "nuts": nuts_code,
        "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
        "limit": limit,
    })).all()
    nodes: dict[str, RelationshipNode] = {}
    edges: list[RelationshipEdge] = []
    table: list[dict[str, Any]] = []
    for row in rows:
        process_key = f"process:{row.process_id}"
        nodes[process_key] = RelationshipNode(id=process_key, node_type="PROCESS", label=row.process_title or str(row.process_id), value=row.value)
        if row.buyer_id and relation_type in (None, "PROCURES") and minimum_confidence <= 1.0 and source in (None, "canonical"):
            buyer_key = f"entity:{row.buyer_id}"
            nodes[buyer_key] = RelationshipNode(id=buyer_key, node_type="BUYER", label=row.buyer_name or str(row.buyer_id))
            edges.append(RelationshipEdge(source=buyer_key, target=process_key, relation_type="PROCURES", value=row.value, confidence=1.0, evidence={"source": "canonical process buyer"}))
        if row.supplier_id and relation_type in (None, "AWARDED_TO") and minimum_confidence <= 1.0 and source in (None, "act_parties"):
            supplier_key = f"entity:{row.supplier_id}"
            nodes[supplier_key] = RelationshipNode(id=supplier_key, node_type="SUPPLIER", label=row.supplier_name or str(row.supplier_id))
            edges.append(RelationshipEdge(source=process_key, target=supplier_key, relation_type="AWARDED_TO", value=row.value, confidence=1.0, evidence={"source": "act_parties"}))
        table.append({"process_id": str(row.process_id), "process": row.process_title, "buyer": row.buyer_name, "supplier": row.supplier_name, "value": row.value, "cpv_codes": row.cpv_codes or []})

    process_ids = [row.process_id for row in rows]
    if process_ids and relation_type in (None, "FUNDED_BY") and source in (None, "funding_links"):
        funding_rows = (await conn.execute(
            sa.select(
                procurement_acts.c.process_id, funding_links.c.funding_project_id,
                funding_links.c.confidence, funding_links.c.link_method,
                funding_projects.c.title, funding_projects.c.budget,
            ).join(procurement_acts, procurement_acts.c.id == funding_links.c.act_id)
            .join(funding_projects, funding_projects.c.id == funding_links.c.funding_project_id)
            .where(
                procurement_acts.c.process_id.in_(process_ids),
                funding_links.c.confidence >= minimum_confidence,
            )
        )).all()
        for funding in funding_rows:
            process_key = f"process:{funding.process_id}"
            funding_key = f"funding:{funding.funding_project_id}"
            nodes[funding_key] = RelationshipNode(
                id=funding_key, node_type="FUNDING",
                label=funding.title or str(funding.funding_project_id), value=funding.budget,
            )
            edges.append(RelationshipEdge(
                source=process_key, target=funding_key, relation_type="FUNDED_BY",
                value=funding.budget, confidence=float(funding.confidence),
                evidence={"source": "funding_links", "method": funding.link_method},
            ))
    return RelationshipResponse(nodes=list(nodes.values()), edges=edges, table=table)


@router.post("/assistant", response_model=AssistantResponse)
async def analytics_assistant(body: AssistantRequest, conn: AsyncConnection = Depends(get_conn)) -> AssistantResponse:
    normalized = body.question.casefold()
    cpv_prefixes = list(dict.fromkeys([
        value.strip().split("-", 1)[0][:8]
        for value in [body.cpv_prefix or "", *body.cpv_prefixes]
        if value.strip()
    ]))
    keyword_patterns = [
        pattern
        for value in dict.fromkeys(body.keywords)
        if value.strip() and (pattern := query_concept_pattern(value)) is not None
    ]
    taxonomy_match_mode = str(body.taxonomy_match or "ANY").upper()
    cpv_likes = [f"{value}%" for value in cpv_prefixes]
    if taxonomy_match_mode == "KEYWORD_REQUIRED" and keyword_patterns:
        cpv_likes = []
    params = {
        "cpv_likes": cpv_likes,
        "keyword_patterns": keyword_patterns,
        "taxonomy_match_all": taxonomy_match_mode in {"ALL", "CPV_AND_KEYWORD"},
        "date_from": body.date_from,
        "date_to": body.date_to,
        "nuts": body.nuts_code,
        "municipality_like": f"%{body.municipality.strip()}%" if body.municipality and body.municipality.strip() else None,
        "amount_min": body.amount_min,
    }
    if any(term in normalized for term in ("χάρτ", "περιοχ", "δήμ", "δημ", "γεωγραφ")):
        rows = (await conn.execute(sa.text(
            """
            SELECT COALESCE(l.municipality_name,l.place_text,l.region_name,l.nuts_code) AS label,
                   ST_Y(ST_Centroid(ST_Collect(l.geom))) AS latitude,
                   ST_X(ST_Centroid(ST_Collect(l.geom))) AS longitude,
                   COUNT(DISTINCT a.id) AS act_count, SUM(a.amount_net) AS value
            FROM act_locations l JOIN procurement_acts a ON a.id=l.act_id
            LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id
            WHERE l.geom IS NOT NULL
              AND procintel_taxonomy_match(
                  a.id,
                  a.title,
                  CAST(:cpv_likes AS TEXT[]),
                  CAST(:keyword_patterns AS TEXT[]),
                  CAST(:taxonomy_match_all AS BOOLEAN)
              )
              AND (CAST(:date_from AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) >= CAST(:date_from AS date))
              AND (CAST(:date_to AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) <= CAST(:date_to AS date))
              AND (CAST(:nuts AS text) IS NULL OR l.nuts_code LIKE CAST(:nuts AS text) || '%')
              AND (
                  CAST(:municipality_like AS text) IS NULL
                  OR l.municipality_name ILIKE CAST(:municipality_like AS text)
                  OR l.place_text ILIKE CAST(:municipality_like AS text)
              )
              AND (CAST(:amount_min AS numeric) IS NULL OR COALESCE(a.amount_gross,a.amount_net,0) >= CAST(:amount_min AS numeric))
            GROUP BY COALESCE(l.municipality_name,l.place_text,l.region_name,l.nuts_code)
            ORDER BY act_count DESC LIMIT 25
            """
        ), params)).mappings().all()
        return AssistantResponse(answer=f"Βρήκα {len(rows)} γεωγραφικές συγκεντρώσεις για τα επιλεγμένα δεδομένα.", intent="GEOGRAPHY", data=[dict(row) for row in rows], visualization={"type": "MAP", "latitude": "latitude", "longitude": "longitude", "size": "act_count"}, methodology="Ομαδοποίηση πραγματικών act_locations με αποθηκευμένο PostGIS geometry.")
    if any(term in normalized for term in ("περισσότερα χρήματα", "top προμηθε", "κορυφαί", "αφμ")):
        rows = (await conn.execute(sa.text(
            """
            SELECT e.id, e.canonical_name, MAX(i.value_normalized) FILTER (WHERE i.scheme='AFM') AS afm,
                   SUM(p.amount) AS recorded_value, COUNT(DISTINCT p.act_id) AS contracts
            FROM act_parties p JOIN entities e ON e.id=p.entity_id
            LEFT JOIN entity_identifiers i ON i.entity_id=e.id AND i.is_current
            JOIN procurement_acts a ON a.id=p.act_id
            LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id
            WHERE p.party_role IN ('SUPPLIER','CONTRACTOR')
              AND procintel_taxonomy_match(
                  a.id,
                  a.title,
                  CAST(:cpv_likes AS TEXT[]),
                  CAST(:keyword_patterns AS TEXT[]),
                  CAST(:taxonomy_match_all AS BOOLEAN)
              )
              AND (CAST(:date_from AS date) IS NULL OR a.decision_date >= CAST(:date_from AS date))
              AND (CAST(:date_to AS date) IS NULL OR a.decision_date <= CAST(:date_to AS date))
              AND (CAST(:amount_min AS numeric) IS NULL OR COALESCE(p.amount,a.amount_gross,a.amount_net,0) >= CAST(:amount_min AS numeric))
            GROUP BY e.id, e.canonical_name ORDER BY recorded_value DESC NULLS LAST LIMIT 20
            """
        ), params)).mappings().all()
        return AssistantResponse(answer="Οι παρακάτω ανάδοχοι έχουν τη μεγαλύτερη καταγεγραμμένη αξία συμβάσεων στο επιλεγμένο scope.", intent="TOP_SUPPLIERS", data=[dict(row) for row in rows], visualization={"type": "BAR", "category": "canonical_name", "value": "recorded_value"}, methodology="Άθροισμα act_parties.amount για επίσημους ρόλους SUPPLIER/CONTRACTOR. Δεν περιλαμβάνει ιδιωτικά έσοδα.")
    if any(term in normalized for term in ("τροποποι", "uplift", "αύξηση αξίας")):
        rows = (await conn.execute(sa.text(
            """
            SELECT m.contract_act_id, a.title, m.amendment_count, m.original_value,
                   m.current_value, m.value_uplift_ratio
            FROM contract_modification_stats m
            JOIN procurement_acts a ON a.id=m.contract_act_id
            WHERE m.amendment_count > 0
            ORDER BY m.value_uplift_ratio DESC NULLS LAST LIMIT 20
            """
        ))).mappings().all()
        return AssistantResponse(answer="Αυτές είναι οι συμβάσεις με τις μεγαλύτερες καταγεγραμμένες αυξήσεις αξίας μετά από συνδεδεμένες τροποποιήσεις.", intent="MODIFICATIONS", data=[dict(row) for row in rows], visualization={"type": "BAR", "category": "title", "value": "value_uplift_ratio"}, methodology="Μόνο επιβεβαιωμένα AMENDS links. Uplift = (τρέχουσα - αρχική αξία) / αρχική αξία.")
    if any(term in normalized for term in ("πληρωμ", "εκτέλεση", "payment")):
        rows = (await conn.execute(sa.text(
            """
            SELECT p.contract_act_id, a.title, p.current_contract_value,
                   p.linked_payment_amount, p.payment_execution_ratio, p.coverage_badge
            FROM payment_execution p JOIN procurement_acts a ON a.id=p.contract_act_id
            ORDER BY p.payment_execution_ratio DESC NULLS LAST LIMIT 20
            """
        ))).mappings().all()
        return AssistantResponse(answer="Η ανάλυση δείχνει συνδεδεμένες εντολές ή δηλωμένες δαπάνες και πάντα συνοδεύεται από ένδειξη κάλυψης.", intent="PAYMENT_EXECUTION", data=[dict(row) for row in rows], visualization={"type": "TABLE"}, methodology="Συνδεδεμένες PAYMENT πράξεις προς τρέχουσα αξία σύμβασης. Δεν ισχυρίζεται επιβεβαιωμένη ταμειακή πληρωμή.")
    if any(term in normalized for term in ("λήγ", "ανανέω", "renewal")):
        rows = (await conn.execute(sa.text(
            """
            SELECT r.contract_act_id, a.title, r.end_date, r.days_to_end,
                   r.avg_lead_time_days, r.renewal_watch_active
            FROM renewal_signals r JOIN procurement_acts a ON a.id=r.contract_act_id
            WHERE r.renewal_watch_active ORDER BY r.end_date LIMIT 20
            """
        ))).mappings().all()
        return AssistantResponse(answer="Αυτές οι συμβάσεις βρίσκονται μέσα στο εκτιμώμενο παράθυρο προετοιμασίας ανανέωσης.", intent="RENEWALS", data=[dict(row) for row in rows], visualization={"type": "TABLE"}, methodology="Ημερομηνία λήξης σε σχέση με τον μέσο χρόνο notice-to-contract του αγοραστή. Είναι σήμα, όχι πρόβλεψη.")
    if any(term in normalized for term in ("χρηματοδοτ", "εσπα", "funding")):
        rows = (await conn.execute(sa.text(
            """
            SELECT id, mis_ops_code, title, program_period, budget, contracted_amount,
                   paid_amount, status FROM funding_projects
            ORDER BY budget DESC NULLS LAST LIMIT 20
            """
        ))).mappings().all()
        return AssistantResponse(answer="Παρουσιάζω τα μεγαλύτερα καταγεγραμμένα χρηματοδοτούμενα έργα και την πρόοδο συμβασιοποίησης/πληρωμών.", intent="FUNDING", data=[dict(row) for row in rows], visualization={"type": "BAR", "category": "title", "value": "budget"}, methodology="Canonical funding_projects και reviewed funding_links από ΑΝΑΠΤΥΞΗ.")
    mart_likes = [f"{value[:4]}%" for value in cpv_prefixes]
    rows = (await conn.execute(
        sa.text(
            """
            SELECT * FROM market_hhi
            WHERE (
                CARDINALITY(CAST(:cpv_likes AS text[])) = 0
                OR cpv_prefix_4 LIKE ANY(CAST(:cpv_likes AS text[]))
            )
            ORDER BY period_year DESC, hhi DESC LIMIT 20
            """
        ),
        {"cpv_likes": mart_likes},
    )).mappings().all()
    return AssistantResponse(answer="Παρουσιάζω τη συγκέντρωση HHI ανά αγορά. Υψηλότερη τιμή σημαίνει μεγαλύτερη συγκέντρωση, όχι ένδειξη εύνοιας.", intent="MARKET_CONCENTRATION", data=[dict(row) for row in rows], visualization={"type": "TABLE"}, methodology="HHI = Σ supplier share² με βάση την καταγεγραμμένη καθαρή αξία συμβάσεων.")

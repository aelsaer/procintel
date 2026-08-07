"""Competitor intelligence built from public procurement evidence.

Confirmed participation and awards are kept distinct from market-based
competitor inference. The API never presents an inferred company as a bidder.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from services.search_index.lexical import query_concept_pattern

from ..db import get_conn
from ..queries import parse_uuid_or_422

router = APIRouter(prefix="/v1/competitors", tags=["competitors"])
process_router = APIRouter(prefix="/v1/processes", tags=["competitors"])


class CompetitorSummary(BaseModel):
    company_id: str
    name: str
    afm: str | None = None
    gemi_number: str | None = None
    company_status: str | None = None
    classification: str
    evidence_level: str
    similarity_score: float
    score_evidence: list[str]
    award_count: int
    bid_count: int
    recorded_value: Decimal | None = None
    buyer_count: int
    shared_buyer_count: int
    head_to_head_count: int
    cpv_codes: list[str]
    nuts_codes: list[str]
    last_activity: date | None = None


class CompetitionCoverage(BaseModel):
    processes_analyzed: int
    companies_found: int
    confirmed_bidder_facts: int
    confirmed_winner_facts: int
    source_note: str


class CompetitionScope(BaseModel):
    cpv_prefixes: list[str]
    keywords: list[str]
    nuts_code: str | None = None
    municipality: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    amount_min: Decimal | None = None
    reference_afm: str | None = None
    taxonomy_match: str = "ANY"


class CompetitorDiscoveryResponse(BaseModel):
    competitors: list[CompetitorSummary]
    coverage: CompetitionCoverage
    scope: CompetitionScope


class CompetitorMetricResponse(BaseModel):
    award_count: int
    bid_count: int
    recorded_value: Decimal | None = None
    buyer_count: int
    head_to_head_count: int


class CompetitorBreakdownResponse(BaseModel):
    key: str
    label: str
    count: int
    recorded_value: Decimal | None = None


class CompetitorActivityResponse(BaseModel):
    activity_id: str
    process_id: str
    public_id: str
    title: str | None = None
    role: str
    evidence_level: str
    event_date: date | None = None
    value: Decimal | None = None
    buyer_name: str | None = None


class CompetitorProfileResponse(BaseModel):
    company_id: str
    name: str
    afm: str | None = None
    gemi_number: str | None = None
    company_status: str | None = None
    legal_form: str | None = None
    metrics: CompetitorMetricResponse
    top_buyers: list[CompetitorBreakdownResponse]
    cpv_distribution: list[CompetitorBreakdownResponse]
    regions: list[CompetitorBreakdownResponse]
    recent_activity: list[CompetitorActivityResponse]


class ProcessParticipantResponse(BaseModel):
    company_id: str | None = None
    name: str
    afm: str | None = None
    role: str
    classification: str
    confidence: float = Field(ge=0, le=1)
    evidence_type: str
    evidence_label: str
    document_id: str | None = None
    source_page: int | None = None


class ProcessCompetitionResponse(BaseModel):
    process_id: str
    confirmed_participants: list[ProcessParticipantResponse]
    likely_incumbent: ProcessParticipantResponse | None = None
    likely_competitors: list[ProcessParticipantResponse]
    coverage_note: str


def _string_list(value) -> list[str]:
    return [str(item) for item in (value or []) if item]


def _query_values(*values: str | None, max_length: int = 160) -> list[str]:
    normalized = [
        item.strip()[:max_length]
        for value in values
        for item in (value or "").split(",")
        if item.strip()
    ]
    return list(dict.fromkeys(normalized))


def _market_score(
    *,
    cpv_prefixes: list[str],
    keywords: list[str],
    nuts_code: str | None,
    amount_min: Decimal | None,
    cpv_codes: list[str],
    nuts_codes: list[str],
    recorded_value: Decimal | None,
    award_count: int,
    shared_buyer_count: int,
    bid_count: int,
    has_reference: bool,
) -> tuple[float, list[str]]:
    score = 20.0
    evidence = ["καταγεγραμμένες αναθέσεις" if award_count else "τεκμηριωμένη συμμετοχή"]
    matching_prefixes = [
        prefix for prefix in cpv_prefixes
        if any(code.startswith(prefix) for code in cpv_codes)
    ]
    if matching_prefixes:
        score += 35
        evidence.append(f"δραστηριότητα σε CPV {', '.join(matching_prefixes[:3])}")
    elif cpv_prefixes or keywords:
        score += 28
        evidence.append("δραστηριότητα σε σχετικό τίτλο του ενεργού προφίλ")
    else:
        score += 18
        evidence.append("ίδιο market συμβάσεων")
    if nuts_code:
        if any(code.upper().startswith(nuts_code.upper()) for code in nuts_codes):
            score += 15
            evidence.append(f"παρουσία σε {nuts_code.upper()}")
    else:
        score += 7
    if amount_min is not None and recorded_value is not None and recorded_value >= amount_min:
        score += 8
        evidence.append("συγκρίσιμη καταγεγραμμένη αξία")
    elif amount_min is None:
        score += 5
    if has_reference and shared_buyer_count:
        score += min(15, 7 + shared_buyer_count * 2)
        evidence.append(f"{shared_buyer_count} κοινοί αγοραστές")
    elif not has_reference:
        score += 5
    if bid_count:
        score += 10
        evidence.append(f"{bid_count} επιβεβαιωμένες συμμετοχές")
    return min(score, 100.0), evidence


async def _entity_id_for_afm(conn: AsyncConnection, afm: str | None):
    if not afm:
        return None
    return (
        await conn.execute(
            sa.text(
                """
                SELECT entity_id
                FROM entity_identifiers
                WHERE scheme = 'AFM' AND value_normalized = :afm AND is_current = TRUE
                LIMIT 1
                """
            ),
            {"afm": afm.strip()},
        )
    ).scalar_one_or_none()


@router.get("/discover", response_model=CompetitorDiscoveryResponse)
async def discover_competitors(
    cpv_prefix: str | None = Query(default=None, max_length=8),
    cpv_prefixes: str | None = Query(default=None, max_length=900),
    nuts_code: str | None = Query(default=None, max_length=8),
    keyword: str | None = Query(default=None, max_length=160),
    keywords: str | None = Query(default=None, max_length=1200),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    municipality: str | None = Query(default=None, max_length=160),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    amount_min: Decimal | None = Query(default=None, ge=0),
    reference_afm: str | None = Query(default=None, min_length=9, max_length=9),
    limit: int = Query(default=30, gt=0, le=100),
    conn: AsyncConnection = Depends(get_conn),
) -> CompetitorDiscoveryResponse:
    reference_id = await _entity_id_for_afm(conn, reference_afm)
    active_cpv_prefixes = _query_values(cpv_prefix, cpv_prefixes, max_length=8)
    active_keywords = _query_values(keyword, keywords)
    cpv_likes = [f"{value.split('-', 1)[0]}%" for value in active_cpv_prefixes]
    keyword_patterns = [
        pattern
        for value in active_keywords
        if (pattern := query_concept_pattern(value)) is not None
    ]
    taxonomy_match_mode = str(taxonomy_match or "ANY").upper()
    if taxonomy_match_mode == "KEYWORD_REQUIRED" and keyword_patterns:
        cpv_likes = []
    taxonomy_match_all = taxonomy_match_mode in {"ALL", "CPV_AND_KEYWORD"}
    municipality_like = f"%{municipality.strip()}%" if municipality and municipality.strip() else None
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH eligible_award_facts AS MATERIALIZED (
                    SELECT
                        a.id AS act_id,
                        a.process_id,
                        party.entity_id,
                        COALESCE(party.amount, a.amount_gross, a.amount_net) AS amount,
                        COALESCE(a.decision_date, a.publication_date, a.submission_date) AS event_date
                    FROM act_parties party
                    JOIN procurement_acts a ON a.id = party.act_id
                    JOIN source_records source ON source.id = a.source_record_id
                    WHERE a.process_id IS NOT NULL
                      AND a.is_current = TRUE
                      AND party.party_role IN ('SUPPLIER', 'CONTRACTOR')
                      AND NOT (
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
                            AND LOWER(COALESCE(issue.object_type, '')) IN ('procurement_act', 'procurement_acts')
                            AND issue.severity IN ('ERROR', 'BLOCKING')
                            AND issue.status <> 'RESOLVED'
                      )
                ), competitive_processes AS (
                    SELECT DISTINCT process_id
                    FROM eligible_award_facts
                    UNION
                    SELECT DISTINCT participation.process_id
                    FROM process_participations participation
                    WHERE participation.entity_id IS NOT NULL
                ), scoped_processes AS (
                    SELECT p.id AS process_id, p.buyer_entity_id
                    FROM competitive_processes competitive
                    JOIN procurement_processes p ON p.id = competitive.process_id
                    WHERE (
                        NOT CAST(:has_act_scope_filter AS BOOLEAN)
                        OR EXISTS (
                        SELECT 1
                        FROM procurement_acts scope_act
                        JOIN source_records scope_source ON scope_source.id = scope_act.source_record_id
                        WHERE scope_act.process_id = p.id
                          AND scope_act.is_current = TRUE
                          AND NOT (
                              scope_source.source_system = 'KHMDHS'
                              AND scope_source.resource_type = 'adamChain'
                              AND scope_act.title IS NULL
                              AND scope_act.amount_net IS NULL
                              AND scope_act.amount_gross IS NULL
                              AND scope_act.publication_date IS NULL
                              AND scope_act.submission_date IS NULL
                              AND scope_act.decision_date IS NULL
                              AND scope_act.start_date IS NULL
                              AND scope_act.end_date IS NULL
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM data_quality_issues issue
                              WHERE issue.object_id = scope_act.id
                                AND LOWER(COALESCE(issue.object_type, '')) IN ('procurement_act', 'procurement_acts')
                                AND issue.severity IN ('ERROR', 'BLOCKING')
                                AND issue.status <> 'RESOLVED'
                          )
                          AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(scope_act.decision_date, scope_act.publication_date, scope_act.submission_date) >= CAST(:date_from AS DATE))
                          AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(scope_act.decision_date, scope_act.publication_date, scope_act.submission_date) <= CAST(:date_to AS DATE))
                          AND (
                              CAST(:amount_min AS NUMERIC) IS NULL
                              OR COALESCE(scope_act.amount_gross, scope_act.amount_net, p.estimated_value, p.awarded_value, 0) >= CAST(:amount_min AS NUMERIC)
                          )
                          AND (
                              NOT CAST(:has_taxonomy_filter AS BOOLEAN)
                              OR procintel_taxonomy_match(
                                  scope_act.id,
                                  scope_act.title,
                                  CAST(:cpv_likes AS TEXT[]),
                                  CAST(:keyword_patterns AS TEXT[]),
                                  CAST(:taxonomy_match_all AS BOOLEAN)
                              )
                          )
                        )
                    )
                      AND (
                          CAST(:nuts_code AS TEXT) IS NULL
                          OR EXISTS (
                              SELECT 1 FROM procurement_acts la
                              JOIN act_locations ll ON ll.act_id = la.id
                              WHERE la.process_id = p.id AND UPPER(ll.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                          )
                      )
                      AND (
                          CAST(:municipality_like AS TEXT) IS NULL
                          OR EXISTS (
                              SELECT 1 FROM procurement_acts ma
                              JOIN act_locations ml ON ml.act_id = ma.id
                              WHERE ma.process_id = p.id
                                AND (
                                    ml.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                    OR ml.place_text ILIKE CAST(:municipality_like AS TEXT)
                                    OR ml.regional_unit_name ILIKE CAST(:municipality_like AS TEXT)
                                )
                          )
                      )
                ), award_facts AS (
                    SELECT DISTINCT ON (a.process_id, a.entity_id, a.act_id)
                        a.process_id,
                        a.entity_id,
                        sp.buyer_entity_id,
                        a.amount,
                        a.event_date
                    FROM eligible_award_facts a
                    JOIN scoped_processes sp ON sp.process_id = a.process_id
                    ORDER BY a.process_id, a.entity_id, a.act_id
                ), participation_facts AS (
                    SELECT DISTINCT
                        pp.process_id,
                        pp.entity_id,
                        sp.buyer_entity_id,
                        pp.participation_role,
                        COALESCE(a.decision_date, a.publication_date, a.submission_date, pp.observed_at::DATE) AS event_date
                    FROM process_participations pp
                    JOIN scoped_processes sp ON sp.process_id = pp.process_id
                    LEFT JOIN procurement_acts a ON a.id = pp.act_id
                    WHERE pp.entity_id IS NOT NULL
                ), company_exposure AS (
                    SELECT process_id, entity_id, buyer_entity_id, event_date FROM award_facts
                    UNION
                    SELECT process_id, entity_id, buyer_entity_id, event_date FROM participation_facts
                ), candidates AS (
                    SELECT entity_id FROM award_facts
                    UNION
                    SELECT entity_id FROM participation_facts
                ), award_aggregates AS (
                    SELECT
                        af.entity_id,
                        COUNT(DISTINCT af.process_id) AS award_count,
                        SUM(af.amount) AS recorded_value
                    FROM award_facts af
                    GROUP BY af.entity_id
                ), exposure_aggregates AS (
                    SELECT
                        ce.entity_id,
                        COUNT(DISTINCT ce.buyer_entity_id) FILTER (WHERE ce.buyer_entity_id IS NOT NULL) AS buyer_count,
                        ARRAY_AGG(DISTINCT ce.buyer_entity_id) FILTER (WHERE ce.buyer_entity_id IS NOT NULL) AS buyer_ids,
                        ARRAY_AGG(DISTINCT ce.process_id) AS process_ids,
                        MAX(ce.event_date) AS last_activity
                    FROM company_exposure ce
                    GROUP BY ce.entity_id
                ), participation_aggregates AS (
                    SELECT entity_id,
                           COUNT(DISTINCT process_id) FILTER (
                               WHERE participation_role IN ('BIDDER', 'CONSORTIUM_MEMBER')
                           )::INT AS bid_count
                    FROM participation_facts
                    GROUP BY entity_id
                ), company_market AS (
                    SELECT
                        c.entity_id,
                        COALESCE(aa.award_count, 0) AS award_count,
                        aa.recorded_value,
                        ea.buyer_count,
                        ea.buyer_ids,
                        ea.process_ids,
                        ea.last_activity,
                        COALESCE(pa.bid_count, 0) AS bid_count
                    FROM candidates c
                    LEFT JOIN award_aggregates aa ON aa.entity_id = c.entity_id
                    JOIN exposure_aggregates ea ON ea.entity_id = c.entity_id
                    LEFT JOIN participation_aggregates pa ON pa.entity_id = c.entity_id
                ), ranked_companies AS (
                    SELECT cm.*, e.canonical_name
                    FROM company_market cm
                    JOIN entities e ON e.id = cm.entity_id
                    WHERE (CAST(:reference_id AS UUID) IS NULL OR cm.entity_id <> CAST(:reference_id AS UUID))
                    ORDER BY (cm.bid_count > 0) DESC, cm.recorded_value DESC NULLS LAST, cm.award_count DESC, e.canonical_name
                    LIMIT :limit
                )
                SELECT
                    cm.entity_id,
                    cm.canonical_name,
                    vat.value_normalized AS afm,
                    company.gemi_number,
                    company.company_status,
                    cm.award_count,
                    cm.recorded_value,
                    cm.buyer_count,
                    cm.buyer_ids,
                    cm.process_ids,
                    cm.last_activity,
                    cm.bid_count,
                    COALESCE(cpv.cpv_codes, ARRAY[]::TEXT[]) AS cpv_codes,
                    COALESCE(nuts.nuts_codes, ARRAY[]::TEXT[]) AS nuts_codes
                FROM ranked_companies cm
                LEFT JOIN LATERAL (
                    SELECT value_normalized FROM entity_identifiers
                    WHERE entity_id = cm.entity_id AND scheme = 'AFM' AND is_current = TRUE
                    ORDER BY confidence DESC LIMIT 1
                ) vat ON TRUE
                LEFT JOIN LATERAL (
                    SELECT gemi_number, company_status FROM entity_company_snapshots
                    WHERE entity_id = cm.entity_id AND is_current = TRUE
                    ORDER BY observed_at DESC LIMIT 1
                ) company ON TRUE
                LEFT JOIN LATERAL (
                    SELECT ARRAY_AGG(DISTINCT cc.cpv_code ORDER BY cc.cpv_code) AS cpv_codes
                    FROM procurement_acts ca
                    JOIN act_cpv_codes cc ON cc.act_id = ca.id
                    WHERE CAST(:include_cpv_footprint AS BOOLEAN)
                      AND ca.process_id = ANY(cm.process_ids)
                ) cpv ON TRUE
                LEFT JOIN LATERAL (
                    SELECT ARRAY_AGG(DISTINCT UPPER(ll.nuts_code) ORDER BY UPPER(ll.nuts_code)) AS nuts_codes
                    FROM procurement_acts la
                    JOIN act_locations ll ON ll.act_id = la.id
                    WHERE CAST(:include_nuts_footprint AS BOOLEAN)
                      AND la.process_id = ANY(cm.process_ids)
                      AND ll.nuts_code IS NOT NULL
                ) nuts ON TRUE
                ORDER BY (cm.bid_count > 0) DESC, cm.recorded_value DESC NULLS LAST, cm.award_count DESC, cm.canonical_name
                """
            ),
            {
                "date_from": date_from,
                "date_to": date_to,
                "amount_min": amount_min,
                "has_act_scope_filter": bool(date_from or date_to or amount_min is not None or cpv_likes or keyword_patterns),
                "has_taxonomy_filter": bool(cpv_likes or keyword_patterns),
                "keyword_patterns": keyword_patterns,
                "taxonomy_match_all": taxonomy_match_all,
                "cpv_likes": cpv_likes,
                "include_cpv_footprint": bool(active_cpv_prefixes),
                "include_nuts_footprint": bool(nuts_code),
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "municipality_like": municipality_like,
                "reference_id": reference_id,
                "limit": limit,
            },
        )
    ).all()

    reference_buyers: set[str] = set()
    reference_processes: set[str] = set()
    if reference_id:
        ref_rows = (
            await conn.execute(
                sa.text(
                    """
                    SELECT DISTINCT a.process_id, p.buyer_entity_id
                    FROM procurement_acts a
                    JOIN procurement_processes p ON p.id = a.process_id
                    LEFT JOIN act_parties ap ON ap.act_id = a.id
                    LEFT JOIN process_participations pp ON pp.process_id = a.process_id
                    WHERE (ap.entity_id = :entity_id OR pp.entity_id = :entity_id)
                      AND a.process_id IS NOT NULL
                    """
                ),
                {"entity_id": reference_id},
            )
        ).all()
        reference_buyers = {str(row.buyer_entity_id) for row in ref_rows if row.buyer_entity_id}
        reference_processes = {str(row.process_id) for row in ref_rows}

    competitors: list[CompetitorSummary] = []
    for row in rows:
        buyer_ids = {str(item) for item in (row.buyer_ids or [])}
        process_ids = {str(item) for item in (row.process_ids or [])}
        shared_buyers = len(buyer_ids & reference_buyers)
        head_to_head = len(process_ids & reference_processes)
        cpv_codes = _string_list(row.cpv_codes)
        nuts_codes = _string_list(row.nuts_codes)
        score, score_evidence = _market_score(
            cpv_prefixes=active_cpv_prefixes,
            keywords=active_keywords,
            nuts_code=nuts_code,
            amount_min=amount_min,
            cpv_codes=cpv_codes,
            nuts_codes=nuts_codes,
            recorded_value=row.recorded_value,
            award_count=row.award_count,
            shared_buyer_count=shared_buyers,
            bid_count=row.bid_count,
            has_reference=reference_id is not None,
        )
        competitors.append(
            CompetitorSummary(
                company_id=str(row.entity_id),
                name=row.canonical_name,
                afm=row.afm,
                gemi_number=row.gemi_number,
                company_status=row.company_status,
                classification="CONFIRMED_BIDDER" if row.bid_count else "CONFIRMED_WINNER",
                evidence_level="CONFIRMED_PARTICIPATION" if row.bid_count else "OFFICIAL_AWARD",
                similarity_score=score,
                score_evidence=score_evidence,
                award_count=row.award_count,
                bid_count=row.bid_count,
                recorded_value=row.recorded_value,
                buyer_count=row.buyer_count,
                shared_buyer_count=shared_buyers,
                head_to_head_count=head_to_head,
                cpv_codes=cpv_codes[:12],
                nuts_codes=nuts_codes[:12],
                last_activity=row.last_activity,
            )
        )
    competitors.sort(key=lambda item: (item.similarity_score, item.recorded_value or Decimal(0)), reverse=True)

    analyzed_processes = {
        str(process_id)
        for row in rows
        for process_id in (row.process_ids or [])
    }
    return CompetitorDiscoveryResponse(
        competitors=competitors,
        coverage=CompetitionCoverage(
            processes_analyzed=len(analyzed_processes),
            companies_found=len(competitors),
            confirmed_bidder_facts=sum(item.bid_count for item in competitors),
            confirmed_winner_facts=sum(item.award_count for item in competitors),
            source_note="Οι συμμετοχές/ανάδοχοι είναι τεκμηριωμένα facts. Οι market competitors είναι εκτίμηση από CPV, αγοραστές, αξία και γεωγραφία.",
        ),
        scope=CompetitionScope(
            cpv_prefixes=active_cpv_prefixes,
            keywords=active_keywords,
            nuts_code=nuts_code,
            municipality=municipality,
            date_from=date_from,
            date_to=date_to,
            amount_min=amount_min,
            reference_afm=reference_afm,
            taxonomy_match=taxonomy_match_mode,
        ),
    )


@router.get("/{company_id}", response_model=CompetitorProfileResponse)
async def get_competitor_profile(
    company_id: str,
    reference_afm: str | None = Query(default=None, min_length=9, max_length=9),
    conn: AsyncConnection = Depends(get_conn),
) -> CompetitorProfileResponse:
    cid = parse_uuid_or_422(company_id, label="company id")
    company = (
        await conn.execute(
            sa.text(
                """
                SELECT e.id, e.canonical_name, vat.value_normalized AS afm,
                       cs.gemi_number, cs.company_status, cs.legal_form
                FROM entities e
                LEFT JOIN LATERAL (
                    SELECT value_normalized FROM entity_identifiers
                    WHERE entity_id = e.id AND scheme = 'AFM' AND is_current = TRUE
                    ORDER BY confidence DESC LIMIT 1
                ) vat ON TRUE
                LEFT JOIN LATERAL (
                    SELECT gemi_number, company_status, legal_form
                    FROM entity_company_snapshots
                    WHERE entity_id = e.id AND is_current = TRUE
                    ORDER BY observed_at DESC LIMIT 1
                ) cs ON TRUE
                WHERE e.id = :cid
                """
            ),
            {"cid": cid},
        )
    ).first()
    if company is None:
        raise HTTPException(status_code=404, detail=f"No company found for id {company_id}")

    metric = (
        await conn.execute(
            sa.text(
                """
                WITH awards AS (
                    SELECT DISTINCT a.process_id, a.id AS act_id, p.buyer_entity_id,
                           COALESCE(ap.amount, a.amount_gross, a.amount_net) AS amount
                    FROM act_parties ap
                    JOIN procurement_acts a ON a.id = ap.act_id
                    JOIN procurement_processes p ON p.id = a.process_id
                    WHERE ap.entity_id = :cid AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
                )
                SELECT COUNT(DISTINCT process_id) AS award_count,
                       SUM(amount) AS recorded_value,
                       COUNT(DISTINCT buyer_entity_id) FILTER (WHERE buyer_entity_id IS NOT NULL) AS buyer_count,
                       (SELECT COUNT(DISTINCT process_id) FROM process_participations
                        WHERE entity_id = :cid AND participation_role IN ('BIDDER', 'CONSORTIUM_MEMBER')) AS bid_count
                FROM awards
                """
            ),
            {"cid": cid},
        )
    ).one()

    reference_id = await _entity_id_for_afm(conn, reference_afm)
    head_to_head = 0
    if reference_id:
        head_to_head = (
            await conn.execute(
                sa.text(
                    """
                    WITH participants AS (
                        SELECT a.process_id, ap.entity_id FROM procurement_acts a
                        JOIN act_parties ap ON ap.act_id = a.id
                        WHERE a.process_id IS NOT NULL AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
                        UNION
                        SELECT process_id, entity_id FROM process_participations WHERE entity_id IS NOT NULL
                    )
                    SELECT COUNT(*) FROM (
                        SELECT process_id FROM participants WHERE entity_id = :cid
                        INTERSECT
                        SELECT process_id FROM participants WHERE entity_id = :reference_id
                    ) shared
                    """
                ),
                {"cid": cid, "reference_id": reference_id},
            )
        ).scalar_one()

    breakdown_sql = {
        "buyers": """
            SELECT p.buyer_entity_id::TEXT AS key, COALESCE(b.canonical_name, 'Άγνωστος φορέας') AS label,
                   COUNT(DISTINCT a.process_id) AS count, SUM(COALESCE(ap.amount, a.amount_gross, a.amount_net)) AS recorded_value
            FROM act_parties ap JOIN procurement_acts a ON a.id = ap.act_id
            JOIN procurement_processes p ON p.id = a.process_id
            LEFT JOIN entities b ON b.id = p.buyer_entity_id
            WHERE ap.entity_id = :cid AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
            GROUP BY p.buyer_entity_id, b.canonical_name ORDER BY recorded_value DESC NULLS LAST, count DESC LIMIT 8
        """,
        "cpv": """
            SELECT cc.cpv_code AS key, cc.cpv_code AS label, COUNT(DISTINCT a.process_id) AS count,
                   SUM(COALESCE(ap.amount, a.amount_gross, a.amount_net)) AS recorded_value
            FROM act_parties ap JOIN procurement_acts a ON a.id = ap.act_id
            JOIN procurement_acts ca ON ca.process_id = a.process_id
            JOIN act_cpv_codes cc ON cc.act_id = ca.id
            WHERE ap.entity_id = :cid AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
            GROUP BY cc.cpv_code ORDER BY count DESC, recorded_value DESC NULLS LAST LIMIT 8
        """,
        "regions": """
            SELECT UPPER(LEFT(ll.nuts_code, 4)) AS key, UPPER(LEFT(ll.nuts_code, 4)) AS label,
                   COUNT(DISTINCT a.process_id) AS count, SUM(COALESCE(ap.amount, a.amount_gross, a.amount_net)) AS recorded_value
            FROM act_parties ap JOIN procurement_acts a ON a.id = ap.act_id
            JOIN procurement_acts la ON la.process_id = a.process_id
            JOIN act_locations ll ON ll.act_id = la.id
            WHERE ap.entity_id = :cid AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR') AND ll.nuts_code IS NOT NULL
            GROUP BY UPPER(LEFT(ll.nuts_code, 4)) ORDER BY count DESC, recorded_value DESC NULLS LAST LIMIT 8
        """,
    }

    async def load_breakdown(sql: str) -> list[CompetitorBreakdownResponse]:
        rows = (await conn.execute(sa.text(sql), {"cid": cid})).all()
        return [
            CompetitorBreakdownResponse(
                key=row.key or "UNKNOWN",
                label=row.label or "Άγνωστο",
                count=row.count,
                recorded_value=row.recorded_value,
            )
            for row in rows
        ]

    activities = (
        await conn.execute(
            sa.text(
                """
                WITH winner_activity AS (
                    SELECT a.id AS activity_id, p.id AS process_id, p.public_id, p.title,
                           'WINNER'::TEXT AS role,
                           'OFFICIAL_SOURCE'::TEXT AS evidence_level,
                           COALESCE(a.decision_date, a.publication_date, a.submission_date) AS event_date,
                           COALESCE(ap.amount, a.amount_gross, a.amount_net) AS value,
                           buyer.canonical_name AS buyer_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY p.id
                               ORDER BY
                                   COALESCE(a.decision_date, a.publication_date, a.submission_date) DESC NULLS LAST,
                                   COALESCE(ap.amount, a.amount_gross, a.amount_net) DESC NULLS LAST,
                                   a.id DESC
                           ) AS activity_rank
                    FROM act_parties ap JOIN procurement_acts a ON a.id = ap.act_id
                    JOIN procurement_processes p ON p.id = a.process_id
                    LEFT JOIN entities buyer ON buyer.id = p.buyer_entity_id
                    WHERE ap.entity_id = :cid AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
                ), participation_activity AS (
                    SELECT pp.id AS activity_id, p.id AS process_id, p.public_id, p.title,
                           pp.participation_role AS role, pp.evidence_type AS evidence_level,
                           COALESCE(a.decision_date, a.publication_date, a.submission_date) AS event_date,
                           COALESCE(a.amount_gross, a.amount_net) AS value,
                           buyer.canonical_name AS buyer_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY p.id, pp.participation_role
                               ORDER BY
                                   COALESCE(a.decision_date, a.publication_date, a.submission_date) DESC NULLS LAST,
                                   pp.observed_at DESC,
                                   pp.id DESC
                           ) AS activity_rank
                    FROM process_participations pp
                    JOIN procurement_processes p ON p.id = pp.process_id
                    LEFT JOIN procurement_acts a ON a.id = pp.act_id
                    LEFT JOIN entities buyer ON buyer.id = p.buyer_entity_id
                    WHERE pp.entity_id = :cid AND pp.participation_role <> 'WINNER'
                ), activity AS (
                    SELECT activity_id, process_id, public_id, title, role, evidence_level,
                           event_date, value, buyer_name
                    FROM winner_activity
                    WHERE activity_rank = 1
                    UNION ALL
                    SELECT activity_id, process_id, public_id, title, role, evidence_level,
                           event_date, value, buyer_name
                    FROM participation_activity
                    WHERE activity_rank = 1
                )
                SELECT * FROM activity
                ORDER BY event_date DESC NULLS LAST, process_id, role
                LIMIT 12
                """
            ),
            {"cid": cid},
        )
    ).all()

    return CompetitorProfileResponse(
        company_id=str(company.id),
        name=company.canonical_name,
        afm=company.afm,
        gemi_number=company.gemi_number,
        company_status=company.company_status,
        legal_form=company.legal_form,
        metrics=CompetitorMetricResponse(
            award_count=metric.award_count,
            bid_count=metric.bid_count,
            recorded_value=metric.recorded_value,
            buyer_count=metric.buyer_count,
            head_to_head_count=head_to_head,
        ),
        top_buyers=await load_breakdown(breakdown_sql["buyers"]),
        cpv_distribution=await load_breakdown(breakdown_sql["cpv"]),
        regions=await load_breakdown(breakdown_sql["regions"]),
        recent_activity=[
            CompetitorActivityResponse(
                activity_id=str(row.activity_id),
                process_id=str(row.process_id),
                public_id=row.public_id,
                title=row.title,
                role=row.role,
                evidence_level=row.evidence_level,
                event_date=row.event_date,
                value=row.value,
                buyer_name=row.buyer_name,
            )
            for row in activities
        ],
    )


@process_router.get("/{process_id}/competition", response_model=ProcessCompetitionResponse)
async def get_process_competition(
    process_id: str,
    conn: AsyncConnection = Depends(get_conn),
) -> ProcessCompetitionResponse:
    pid = parse_uuid_or_422(process_id, label="process id")
    process = (
        await conn.execute(
            sa.text(
                """
                SELECT id, buyer_entity_id, primary_cpv_code,
                       COALESCE(first_observed_at::DATE, created_at::DATE) AS event_date
                FROM procurement_processes WHERE id = :pid
                """
            ),
            {"pid": pid},
        )
    ).first()
    if process is None:
        raise HTTPException(status_code=404, detail=f"No process found for id {process_id}")

    known_rows = (
        await conn.execute(
            sa.text(
                """
                WITH known AS (
                    SELECT pp.entity_id, COALESCE(e.canonical_name, pp.participant_name_raw, pp.participant_afm_raw) AS name,
                           COALESCE(vat.value_normalized, pp.participant_afm_raw) AS afm,
                           pp.participation_role AS role, pp.confidence, pp.evidence_type,
                           pp.document_id, pp.source_page,
                           CASE WHEN pp.evidence_type = 'DOCUMENT_EXTRACTED'
                                THEN 'Ρόλος και ΑΦΜ σε αποθηκευμένο έγγραφο'
                                WHEN pp.participation_role = 'WINNER'
                                THEN 'Επίσημη εγγραφή ανάθεσης'
                                ELSE 'Επίσημη εγγραφή συμμετοχής' END AS evidence_label
                    FROM process_participations pp
                    LEFT JOIN entities e ON e.id = pp.entity_id
                    LEFT JOIN LATERAL (
                        SELECT value_normalized FROM entity_identifiers
                        WHERE entity_id = pp.entity_id AND scheme = 'AFM' AND is_current = TRUE LIMIT 1
                    ) vat ON TRUE
                    WHERE pp.process_id = :pid
                    UNION ALL
                    SELECT ap.entity_id, e.canonical_name, vat.value_normalized, 'WINNER', 1.0,
                           'OFFICIAL_SOURCE', NULL::UUID, NULL::INTEGER, 'Επίσημη εγγραφή ανάθεσης'
                    FROM procurement_acts a JOIN act_parties ap ON ap.act_id = a.id
                    JOIN entities e ON e.id = ap.entity_id
                    LEFT JOIN LATERAL (
                        SELECT value_normalized FROM entity_identifiers
                        WHERE entity_id = ap.entity_id AND scheme = 'AFM' AND is_current = TRUE LIMIT 1
                    ) vat ON TRUE
                    WHERE a.process_id = :pid AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
                      AND NOT EXISTS (
                          SELECT 1 FROM process_participations pp
                          WHERE pp.process_id = :pid AND pp.entity_id = ap.entity_id AND pp.participation_role = 'WINNER'
                      )
                )
                SELECT DISTINCT ON (COALESCE(entity_id::TEXT, afm, name), role) *
                FROM known
                ORDER BY COALESCE(entity_id::TEXT, afm, name), role, confidence DESC
                """
            ),
            {"pid": pid},
        )
    ).all()

    confirmed = [
        ProcessParticipantResponse(
            company_id=str(row.entity_id) if row.entity_id else None,
            name=row.name,
            afm=row.afm,
            role=row.role,
            classification="CONFIRMED_PARTICIPANT" if row.role != "WINNER" else "CONFIRMED_WINNER",
            confidence=float(row.confidence),
            evidence_type=row.evidence_type,
            evidence_label=row.evidence_label,
            document_id=str(row.document_id) if row.document_id else None,
            source_page=row.source_page,
        )
        for row in known_rows
    ]
    excluded_ids = [row.entity_id for row in known_rows if row.entity_id]

    likely_rows = (
        await conn.execute(
            sa.text(
                """
                WITH supplier_history AS (
                    SELECT ap.entity_id, e.canonical_name, vat.value_normalized AS afm,
                           COUNT(DISTINCT a.process_id) AS award_count,
                           MAX(COALESCE(a.decision_date, a.publication_date, a.submission_date)) AS last_activity,
                           BOOL_OR(p.buyer_entity_id = CAST(:buyer_id AS UUID)) AS same_buyer,
                           BOOL_OR(
                               CAST(:cpv_prefix AS TEXT) IS NOT NULL AND (
                                   COALESCE(p.primary_cpv_code, '') LIKE CAST(:cpv_like AS TEXT)
                                   OR EXISTS (
                                       SELECT 1 FROM procurement_acts ca JOIN act_cpv_codes cc ON cc.act_id = ca.id
                                       WHERE ca.process_id = p.id AND cc.cpv_code LIKE CAST(:cpv_like AS TEXT)
                                   )
                               )
                           ) AS same_cpv
                    FROM act_parties ap
                    JOIN procurement_acts a ON a.id = ap.act_id
                    JOIN procurement_processes p ON p.id = a.process_id
                    JOIN entities e ON e.id = ap.entity_id
                    LEFT JOIN LATERAL (
                        SELECT value_normalized FROM entity_identifiers
                        WHERE entity_id = ap.entity_id AND scheme = 'AFM' AND is_current = TRUE LIMIT 1
                    ) vat ON TRUE
                    WHERE ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
                      AND a.process_id <> :pid
                      AND (CAST(:buyer_id AS UUID) IS NOT NULL OR CAST(:cpv_prefix AS TEXT) IS NOT NULL)
                      AND (
                          p.buyer_entity_id = CAST(:buyer_id AS UUID)
                          OR COALESCE(p.primary_cpv_code, '') LIKE CAST(:cpv_like AS TEXT)
                          OR EXISTS (
                              SELECT 1 FROM procurement_acts ca JOIN act_cpv_codes cc ON cc.act_id = ca.id
                              WHERE ca.process_id = p.id AND cc.cpv_code LIKE CAST(:cpv_like AS TEXT)
                          )
                      )
                      AND NOT (ap.entity_id = ANY(CAST(:excluded_ids AS UUID[])))
                    GROUP BY ap.entity_id, e.canonical_name, vat.value_normalized
                )
                SELECT * FROM supplier_history
                ORDER BY (same_buyer::INT + same_cpv::INT) DESC, award_count DESC, last_activity DESC NULLS LAST
                LIMIT 8
                """
            ),
            {
                "pid": pid,
                "buyer_id": process.buyer_entity_id,
                "cpv_prefix": process.primary_cpv_code[:3] if process.primary_cpv_code else None,
                "cpv_like": f"{process.primary_cpv_code[:3]}%" if process.primary_cpv_code else None,
                "excluded_ids": excluded_ids,
            },
        )
    ).all()

    likely = []
    for row in likely_rows:
        evidence = []
        if row.same_buyer:
            evidence.append("προηγούμενος ανάδοχος του ίδιου φορέα")
        if row.same_cpv:
            evidence.append("αναθέσεις στο ίδιο CPV market")
        confidence = 0.72 if row.same_buyer and row.same_cpv else 0.56
        likely.append(
            ProcessParticipantResponse(
                company_id=str(row.entity_id),
                name=row.canonical_name,
                afm=row.afm,
                role="POTENTIAL_COMPETITOR",
                classification="INFERRED_MARKET_COMPETITOR",
                confidence=confidence,
                evidence_type="MARKET_INFERENCE",
                evidence_label=" · ".join(evidence),
            )
        )

    incumbent = next((item for item in likely if "ίδιου φορέα" in item.evidence_label), None)
    return ProcessCompetitionResponse(
        process_id=process_id,
        confirmed_participants=confirmed,
        likely_incumbent=incumbent,
        likely_competitors=likely,
        coverage_note=(
            "Επιβεβαιωμένοι συμμετέχοντες εμφανίζονται μόνο όταν υπάρχει επίσημη εγγραφή ή ρητή αναφορά ρόλου+ΑΦΜ σε έγγραφο. "
            "Οι υπόλοιπες εταιρείες είναι market inference και όχι ισχυρισμός συμμετοχής."
        ),
    )

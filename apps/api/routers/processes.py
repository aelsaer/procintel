"""GET /v1/processes/{id}, GET /v1/processes/{id}/timeline — description.txt
§30.1. `/v1/processes/{id}` is a thin pass-through over `procurement_360`
(db/marts/procurement_360.sql); the timeline endpoint queries
`procurement_acts` directly for independent, explicit date ordering.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import procurement_acts
from packages.schemas.responses import ProcessDetailResponse, ProcessTimelineResponse, TimelineNode
from services.intelligence.tender_brief import load_tender_publication_bundle

from ..db import get_conn
from ..queries import load_identifiers, parse_uuid_or_422

router = APIRouter(prefix="/v1/processes", tags=["processes"])


class SimilarContractResponse(BaseModel):
    process_id: str
    title: str | None
    buyer_name: str | None
    contract_value: float | None
    decision_date: Any | None
    cpv_codes: list[str]
    similarity_score: float
    reasons: list[str]


def _maybe_json(value: Any) -> Any:
    """procurement_360's aggregated columns are JSONB; asyncpg/SQLAlchemy
    usually decode those automatically, but this endpoint goes through a
    raw `text()` query (the view isn't Core-mapped) so decoding isn't
    guaranteed — handle both a pre-decoded value and a raw JSON string."""
    if isinstance(value, (dict, list)) or value is None:
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


@router.get("/{process_id}", response_model=ProcessDetailResponse)
async def get_process(process_id: str, conn: AsyncConnection = Depends(get_conn)) -> ProcessDetailResponse:
    pid = parse_uuid_or_422(process_id, label="process id")
    row = (
        (await conn.execute(text("SELECT * FROM procurement_360 WHERE process_id = :pid"), {"pid": str(pid)}))
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No process found for id {process_id}")

    data = dict(row)
    buyer = _maybe_json(data["buyer"]) or {}
    locations = _maybe_json(data["locations"]) or []
    publication = await load_tender_publication_bundle(
        conn,
        process_id=pid,
        buyer_name=buyer.get("name"),
        fallback_title=data["title"],
        locations=locations,
    )
    return ProcessDetailResponse(
        process_id=str(data["process_id"]),
        public_id=data["public_id"],
        title=data["title"],
        lifecycle_status=data["lifecycle_status"],
        record_status=data["record_status"],
        estimated_value=data["estimated_value"],
        awarded_value=data["awarded_value"],
        current_contract_value=data["current_contract_value"],
        currency=data["currency"] or "EUR",
        buyer=buyer,
        suppliers=_maybe_json(data["suppliers"]) or [],
        supplier_company_info=_maybe_json(data["supplier_company_info"]) or [],
        acts=_maybe_json(data["acts"]) or [],
        lots=_maybe_json(data["lots"]) or [],
        documents=publication["documents"],
        diavgeia_decisions=_maybe_json(data["diavgeia_decisions"]) or [],
        ted_notices=_maybe_json(data["ted_notices"]) or [],
        funding_projects=_maybe_json(data["funding_projects"]) or [],
        mef_expense_signals=_maybe_json(data["mef_expense_signals"]) or [],
        locations=locations,
        data_quality=_maybe_json(data["data_quality"]) or {},
        summary=publication["summary"],
        official_records=publication["official_records"],
        first_observed_at=data["first_observed_at"],
        last_observed_at=data["last_observed_at"],
    )


@router.get("/{process_id}/timeline", response_model=ProcessTimelineResponse)
async def get_process_timeline(
    process_id: str, conn: AsyncConnection = Depends(get_conn)
) -> ProcessTimelineResponse:
    pid = parse_uuid_or_422(process_id, label="process id")
    rows = (
        await conn.execute(
            select(procurement_acts)
            .where(
                procurement_acts.c.process_id == pid,
                func.procintel_act_is_analytics_eligible(
                    procurement_acts.c.id
                ),
            )
            .order_by(procurement_acts.c.publication_date.asc().nulls_last())
        )
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No process found for id {process_id}")

    nodes = []
    for act_row in rows:
        identifiers = await load_identifiers(conn, act_row.id)
        act_date = act_row.publication_date or act_row.decision_date or act_row.submission_date
        nodes.append(
            TimelineNode(
                act_id=str(act_row.id),
                act_type=act_row.act_type,
                title=act_row.title,
                event_date=act_date,
                amount_gross=act_row.amount_gross,
                status=act_row.status,
                identifiers=identifiers,
            )
        )

    return ProcessTimelineResponse(process_id=process_id, nodes=nodes)


@router.get("/{process_id}/similar-contracts", response_model=list[SimilarContractResponse])
async def get_similar_contracts(
    process_id: str,
    conn: AsyncConnection = Depends(get_conn),
) -> list[SimilarContractResponse]:
    pid = parse_uuid_or_422(process_id, label="process id")
    rows = (await conn.execute(text(
        """
        WITH target AS (
            SELECT pp.id, pp.title, pp.buyer_entity_id,
                   COALESCE(pp.current_contract_value,pp.awarded_value,pp.estimated_value) AS value,
                   ARRAY_REMOVE(ARRAY_AGG(DISTINCT cpv.cpv_code),NULL) AS cpv_codes
            FROM procurement_processes pp
            LEFT JOIN procurement_acts a ON a.process_id=pp.id AND a.is_current=TRUE
            LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id
            WHERE pp.id=CAST(:process_id AS uuid)
            GROUP BY pp.id,pp.title,pp.buyer_entity_id,pp.current_contract_value,pp.awarded_value,pp.estimated_value
        ), candidates AS (
            SELECT pp.id, pp.title, pp.buyer_entity_id, buyer.canonical_name AS buyer_name,
                   COALESCE(pp.current_contract_value,pp.awarded_value,pp.estimated_value,MAX(a.amount_net)) AS contract_value,
                   MAX(a.decision_date) AS decision_date,
                   ARRAY_REMOVE(ARRAY_AGG(DISTINCT cpv.cpv_code),NULL) AS cpv_codes,
                   BOOL_OR(cpv.cpv_code = ANY(t.cpv_codes)) AS cpv_overlap,
                   pp.buyer_entity_id=t.buyer_entity_id AS same_buyer,
                   similarity(COALESCE(pp.title,''),COALESCE(t.title,'')) AS title_similarity,
                   t.value AS target_value
            FROM target t CROSS JOIN procurement_processes pp
            JOIN procurement_acts a ON a.process_id=pp.id AND a.is_current=TRUE AND a.act_type='CONTRACT'
            LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id
            LEFT JOIN entities buyer ON buyer.id=pp.buyer_entity_id
            WHERE pp.id<>t.id
            GROUP BY pp.id,pp.title,pp.buyer_entity_id,buyer.canonical_name,
                     pp.current_contract_value,pp.awarded_value,pp.estimated_value,
                     t.cpv_codes,t.buyer_entity_id,t.title,t.value
        )
        SELECT *,
               (CASE WHEN cpv_overlap THEN 0.45 ELSE 0 END
                + CASE WHEN same_buyer THEN 0.30 ELSE 0 END
                + 0.15*title_similarity
                + CASE WHEN target_value IS NOT NULL AND contract_value IS NOT NULL
                         AND contract_value BETWEEN target_value*0.6 AND target_value*1.4 THEN 0.10 ELSE 0 END) AS similarity_score
        FROM candidates
        WHERE cpv_overlap OR same_buyer OR title_similarity>=0.45
        ORDER BY similarity_score DESC, decision_date DESC NULLS LAST LIMIT 12
        """
    ), {"process_id": str(pid)})).mappings().all()
    return [SimilarContractResponse(
        process_id=str(row["id"]), title=row["title"], buyer_name=row["buyer_name"],
        contract_value=float(row["contract_value"]) if row["contract_value"] is not None else None,
        decision_date=row["decision_date"], cpv_codes=row["cpv_codes"] or [],
        similarity_score=round(float(row["similarity_score"]), 3),
        reasons=[
            *(["Ίδιο κύριο CPV"] if row["cpv_overlap"] else []),
            *(["Ίδια αναθέτουσα αρχή"] if row["same_buyer"] else []),
            *(["Παρόμοιος τίτλος"] if float(row["title_similarity"]) >= 0.45 else []),
            *(["Παρόμοια αξία"] if row["target_value"] is not None and row["contract_value"] is not None and float(row["target_value"]) * 0.6 <= float(row["contract_value"]) <= float(row["target_value"]) * 1.4 else []),
        ],
    ) for row in rows]

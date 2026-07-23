"""GET /v1/contracts/{identifier} — description.txt §30.1, §30.4.

Operates at the single-act level (works even before adamChain has assigned
a process_id) — process-level aggregation is `/v1/processes/{id}`.
`build_contract_response` is also reused by the companies router's
`/v1/companies/{id}/contracts`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import act_identifiers, procurement_acts, source_records
from packages.schemas.responses import AmountsResponse, ContractResponse, PartyResponse, ProvenanceEntry
from services.intelligence.tender_brief import load_tender_publication_bundle

from ..db import get_conn
from ..queries import load_identifiers, load_parties

router = APIRouter(prefix="/v1/contracts", tags=["contracts"])


async def _load_act_by_identifier(conn: AsyncConnection, identifier_normalized: str) -> Row | None:
    return (
        await conn.execute(
            select(procurement_acts)
            .select_from(procurement_acts.join(act_identifiers, act_identifiers.c.act_id == procurement_acts.c.id))
            .where(
                act_identifiers.c.scheme.in_(("ADAM", "ADA")),
                act_identifiers.c.value_normalized == identifier_normalized,
            )
        )
    ).first()


async def build_contract_response(conn: AsyncConnection, act_row: Row) -> ContractResponse:
    identifiers = await load_identifiers(conn, act_row.id)
    buyer_dict, supplier_dicts = await load_parties(conn, act_row.id)

    source_record = (
        await conn.execute(select(source_records).where(source_records.c.id == act_row.source_record_id))
    ).first()
    provenance: list[ProvenanceEntry] = []
    if source_record is not None:
        provenance.append(
            ProvenanceEntry(
                source=source_record.source_system,
                source_native_id=source_record.source_native_id,
                retrieved_at=source_record.fetched_at,
            )
        )

    publication = await load_tender_publication_bundle(
        conn,
        act_id=act_row.id,
        buyer_name=buyer_dict["name"] if buyer_dict else None,
        fallback_title=act_row.title,
    )
    return ContractResponse(
        id=str(act_row.id),
        process_id=str(act_row.process_id) if act_row.process_id else None,
        act_type=act_row.act_type,
        title=act_row.title,
        status=act_row.status,
        procedure_type=act_row.procedure_type,
        identifiers=identifiers,
        buyer=PartyResponse(**buyer_dict) if buyer_dict else None,
        suppliers=[PartyResponse(**s) for s in supplier_dicts],
        amounts=AmountsResponse(
            net=act_row.amount_net,
            vat=act_row.vat_amount,
            gross=act_row.amount_gross,
            currency=act_row.currency or "EUR",
        ),
        provenance=provenance,
        summary=publication["summary"],
        official_records=publication["official_records"],
        documents=publication["documents"],
    )


@router.get("/{identifier}", response_model=ContractResponse)
async def get_contract(identifier: str, conn: AsyncConnection = Depends(get_conn)) -> ContractResponse:
    identifier_normalized = identifier.strip().upper()
    act_row = await _load_act_by_identifier(conn, identifier_normalized)
    if act_row is None:
        raise HTTPException(status_code=404, detail=f"No act found for identifier {identifier_normalized}")
    return await build_contract_response(conn, act_row)

"""GET /v1/companies/{id}, GET /v1/companies/{id}/contracts —
description.txt §5.3, §30.1. `legal_form`/`company_status` stay None until
the ΓΕΜΗ connector populates `entity_company_snapshots` — that's a data-gap,
not a bug, and is left explicit rather than guessed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import act_parties, entities, procurement_acts
from packages.schemas.responses import CompanyContractsResponse, CompanySummaryResponse

from ..db import get_conn
from ..queries import load_entity_vat, parse_uuid_or_422
from .contracts import build_contract_response

router = APIRouter(prefix="/v1/companies", tags=["companies"])


@router.get("/{company_id}", response_model=CompanySummaryResponse)
async def get_company(company_id: str, conn: AsyncConnection = Depends(get_conn)) -> CompanySummaryResponse:
    cid = parse_uuid_or_422(company_id, label="company id")

    entity_row = (await conn.execute(select(entities).where(entities.c.id == cid))).first()
    if entity_row is None:
        raise HTTPException(status_code=404, detail=f"No entity found for id {company_id}")

    vat = await load_entity_vat(conn, cid)

    agg_row = (
        await conn.execute(
            select(
                func.sum(act_parties.c.amount).label("total_value"),
                func.count(func.distinct(act_parties.c.act_id)).label("contract_count"),
            ).where(
                act_parties.c.entity_id == cid,
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
            )
        )
    ).first()

    return CompanySummaryResponse(
        id=str(cid),
        name=entity_row.canonical_name,
        vat=vat,
        legal_form=None,
        company_status=None,
        total_public_sector_value=agg_row.total_value if agg_row is not None else None,
        contract_count=agg_row.contract_count if agg_row is not None else 0,
    )


@router.get("/{company_id}/contracts", response_model=CompanyContractsResponse)
async def get_company_contracts(
    company_id: str, conn: AsyncConnection = Depends(get_conn)
) -> CompanyContractsResponse:
    cid = parse_uuid_or_422(company_id, label="company id")

    act_rows = (
        await conn.execute(
            select(procurement_acts)
            .select_from(act_parties.join(procurement_acts, procurement_acts.c.id == act_parties.c.act_id))
            .where(
                act_parties.c.entity_id == cid,
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
            )
            .distinct()
        )
    ).all()

    contracts = [await build_contract_response(conn, act_row) for act_row in act_rows]
    return CompanyContractsResponse(company_id=str(cid), contracts=contracts)

"""GET /v1/buyers/{id}, GET /v1/buyers/{id}/suppliers — description.txt
§5.2, §30.1. A thin first cut of buyer intelligence: total contract value
and count, and a supplier ranking by value. The fuller profile
(§5.2's CPV mix, procurement calendar, concentration, ...) is
services/analytics territory (db/marts/analytics_marts.sql), not this
endpoint — this is the direct-query baseline underneath it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import act_parties, entities, entity_identifiers, procurement_acts
from packages.schemas.responses import BuyerSuppliersResponse, BuyerSummaryResponse, SupplierShareResponse

from ..db import get_conn
from ..queries import load_entity_vat, parse_uuid_or_422

router = APIRouter(prefix="/v1/buyers", tags=["buyers"])


@router.get("/{buyer_id}", response_model=BuyerSummaryResponse)
async def get_buyer(buyer_id: str, conn: AsyncConnection = Depends(get_conn)) -> BuyerSummaryResponse:
    bid = parse_uuid_or_422(buyer_id, label="buyer id")

    entity_row = (await conn.execute(select(entities).where(entities.c.id == bid))).first()
    if entity_row is None:
        raise HTTPException(status_code=404, detail=f"No entity found for id {buyer_id}")

    vat = await load_entity_vat(conn, bid)

    agg_row = (
        await conn.execute(
            select(
                func.sum(procurement_acts.c.amount_gross).label("total_value"),
                func.count(func.distinct(procurement_acts.c.id)).label("contract_count"),
            )
            .select_from(act_parties.join(procurement_acts, procurement_acts.c.id == act_parties.c.act_id))
            .where(
                act_parties.c.entity_id == bid,
                act_parties.c.party_role.in_(("BUYER", "CONTRACTING_AUTHORITY")),
                procurement_acts.c.act_type == "CONTRACT",
            )
        )
    ).first()

    return BuyerSummaryResponse(
        id=str(bid),
        name=entity_row.canonical_name,
        vat=vat,
        total_contract_value=agg_row.total_value if agg_row is not None else None,
        contract_count=agg_row.contract_count if agg_row is not None else 0,
    )


@router.get("/{buyer_id}/suppliers", response_model=BuyerSuppliersResponse)
async def get_buyer_suppliers(
    buyer_id: str, conn: AsyncConnection = Depends(get_conn)
) -> BuyerSuppliersResponse:
    bid = parse_uuid_or_422(buyer_id, label="buyer id")

    buyer_acts = select(act_parties.c.act_id).where(
        act_parties.c.entity_id == bid,
        act_parties.c.party_role.in_(("BUYER", "CONTRACTING_AUTHORITY")),
    )

    supplier_party = act_parties.alias("supplier_party")
    rows = (
        await conn.execute(
            select(
                entities.c.id,
                entities.c.canonical_name,
                func.sum(supplier_party.c.amount).label("value"),
                func.count(func.distinct(supplier_party.c.act_id)).label("contract_count"),
            )
            .select_from(supplier_party.join(entities, entities.c.id == supplier_party.c.entity_id))
            .where(
                supplier_party.c.act_id.in_(buyer_acts),
                supplier_party.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
            )
            .group_by(entities.c.id, entities.c.canonical_name)
            .order_by(func.sum(supplier_party.c.amount).desc().nulls_last())
        )
    ).all()

    suppliers = []
    for row in rows:
        vat = await load_entity_vat(conn, row.id)
        suppliers.append(
            SupplierShareResponse(
                id=str(row.id),
                name=row.canonical_name,
                vat=vat,
                value=row.value,
                contract_count=row.contract_count,
            )
        )

    return BuyerSuppliersResponse(buyer_id=str(bid), suppliers=suppliers)

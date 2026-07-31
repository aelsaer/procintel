"""GET /v1/companies/{id}, GET /v1/companies/{id}/contracts —
description.txt §5.3, §30.1. `legal_form`/`company_status` stay None until
the ΓΕΜΗ connector populates `entity_company_snapshots` — that's a data-gap,
not a bug, and is left explicit rather than guessed.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    act_parties,
    entities,
    entity_company_snapshots,
    procurement_acts,
)
from packages.schemas.responses import CompanyContractsResponse, CompanySummaryResponse
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.gemi.client import GemiClient
from services.ingestion.connectors.gemi.config import GemiConnectorConfig
from services.ingestion.connectors.gemi.provider import (
    CompanySearchQuery,
    GemiCompanyRegistryProvider,
)
from services.ingestion.connectors.gemi.resolve import resolve_company_by_gemi

from ..auth import get_current_user, require_role
from ..db import get_conn
from ..queries import load_entity_vat, parse_uuid_or_422
from .contracts import build_contract_response

router = APIRouter(prefix="/v1/companies", tags=["companies"])


def _registry_provider() -> tuple[GemiClient, GemiCompanyRegistryProvider]:
    try:
        client = GemiClient(GemiConnectorConfig.from_env())
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="ΓΕΜΗ OpenData is not configured on this deployment",
        ) from exc
    return client, GemiCompanyRegistryProvider(client)


@router.get("/registry/search", response_model=list[dict])
async def search_gemi_registry(
    name: str | None = Query(default=None, max_length=300),
    kad: str | None = Query(default=None, max_length=30),
    status: str | None = Query(default=None, max_length=80),
    prefecture: str | None = Query(default=None, max_length=80),
    municipality: str | None = Query(default=None, max_length=80),
    _: AuthenticatedUser = Depends(get_current_user),
) -> list[dict]:
    if not any((name, kad, status, prefecture, municipality)):
        raise HTTPException(status_code=422, detail="at least one registry filter is required")
    client, provider = _registry_provider()
    try:
        companies = await provider.search(
            CompanySearchQuery(
                name=name,
                kad=kad,
                status=status,
                prefecture=prefecture,
                municipality=municipality,
            )
        )
        return [company.model_dump(mode="json") for company in companies]
    finally:
        await client.aclose()


@router.post("/registry/resolve/{gemi_number}", response_model=dict)
async def resolve_gemi_registry_number(
    gemi_number: str,
    conn: AsyncConnection = Depends(get_conn),
    _: AuthenticatedUser = Depends(
        require_role("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")
    ),
) -> dict:
    client, provider = _registry_provider()
    try:
        result = await resolve_company_by_gemi(
            conn,
            provider=provider,
            raw_store=LocalFilesystemRawStore(
                os.environ.get("RAW_STORAGE_ROOT", "./raw")
            ),
            gemi_number=gemi_number,
        )
    finally:
        await client.aclose()
    if result is None:
        raise HTTPException(status_code=404, detail="ΓΕΜΗ company not found")
    return {
        "entity_id": str(result.entity_id),
        "snapshot_id": str(result.snapshot_id) if result.snapshot_id else None,
        "wrote_new_snapshot": result.wrote_new_snapshot,
    }


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

    snapshot = (
        await conn.execute(
            select(
                entity_company_snapshots.c.legal_form,
                entity_company_snapshots.c.company_status,
            ).where(
                entity_company_snapshots.c.entity_id == cid,
                entity_company_snapshots.c.is_current.is_(True),
            )
        )
    ).first()

    return CompanySummaryResponse(
        id=str(cid),
        name=entity_row.canonical_name,
        vat=vat,
        legal_form=snapshot.legal_form if snapshot else None,
        company_status=snapshot.company_status if snapshot else None,
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

"""Framework agreement and route-to-market intelligence."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    act_cpv_codes,
    act_identifiers,
    act_links,
    act_locations,
    act_parties,
    entities,
    framework_supplier_memberships,
    framework_watches,
    procurement_acts,
    procurement_processes,
)
from services.intelligence.frameworks import (
    framework_relevance_score,
    framework_window_status,
)
from services.intelligence.tender_brief import links_for_display_identifier

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..queries import parse_uuid_or_422
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/frameworks", tags=["frameworks"])
_EDIT_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")


class FrameworkSupplierResponse(BaseModel):
    entity_id: str
    name: str
    lot_identifier: str | None
    awarded_value: Decimal | None
    membership_status: str


class FrameworkResponse(BaseModel):
    act_id: str
    process_id: str | None
    public_id: str | None
    title: str
    buyer_id: str | None
    buyer_name: str | None
    cpv_codes: list[str]
    status: str
    publication_date: date | None
    valid_from: date | None
    valid_until: date | None
    days_to_expiry: int | None
    ceiling_amount: Decimal | None
    realized_spend: Decimal
    utilization: float | None
    call_off_count: int
    suppliers: list[FrameworkSupplierResponse]
    buyer_count: int
    relevance_score: float
    official_identifier: str | None
    official_url: str | None
    watched: bool
    watch_id: str | None
    notify_before_days: int | None


class FrameworkListResponse(BaseModel):
    generated_at: datetime
    summary: dict[str, int | float | str | None]
    frameworks: list[FrameworkResponse]
    methodology: list[str]


class FrameworkWatchRequest(BaseModel):
    notify_before_days: int = Field(default=90, ge=7, le=730)


def _csv_values(value: str | None) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in (value or "").split(",") if item.strip()))


async def _framework_suppliers(
    conn: AsyncConnection,
    framework_act_id: uuid.UUID,
) -> list[FrameworkSupplierResponse]:
    membership_rows = (
        await conn.execute(
            sa.select(
                framework_supplier_memberships.c.supplier_entity_id,
                entities.c.canonical_name,
                framework_supplier_memberships.c.lot_identifier,
                framework_supplier_memberships.c.awarded_value,
                framework_supplier_memberships.c.membership_status,
            )
            .join(entities, entities.c.id == framework_supplier_memberships.c.supplier_entity_id)
            .where(framework_supplier_memberships.c.framework_act_id == framework_act_id)
        )
    ).all()
    suppliers: dict[tuple[uuid.UUID, str | None], FrameworkSupplierResponse] = {
        (row.supplier_entity_id, row.lot_identifier): FrameworkSupplierResponse(
            entity_id=str(row.supplier_entity_id),
            name=row.canonical_name,
            lot_identifier=row.lot_identifier,
            awarded_value=row.awarded_value,
            membership_status=row.membership_status,
        )
        for row in membership_rows
    }
    direct_rows = (
        await conn.execute(
            sa.select(
                act_parties.c.entity_id,
                entities.c.canonical_name,
                act_parties.c.lot_id,
                act_parties.c.amount,
            )
            .join(entities, entities.c.id == act_parties.c.entity_id)
            .where(
                act_parties.c.act_id == framework_act_id,
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR", "CONSORTIUM_MEMBER")),
            )
        )
    ).all()
    for row in direct_rows:
        lot = str(row.lot_id) if row.lot_id else None
        suppliers.setdefault(
            (row.entity_id, lot),
            FrameworkSupplierResponse(
                entity_id=str(row.entity_id),
                name=row.canonical_name,
                lot_identifier=lot,
                awarded_value=row.amount,
                membership_status="PUBLISHED",
            ),
        )
    return sorted(suppliers.values(), key=lambda item: item.name)


async def _call_offs(
    conn: AsyncConnection,
    *,
    framework_act_id: uuid.UUID,
    process_id: uuid.UUID | None,
) -> tuple[int, Decimal, int]:
    linked_ids = sa.select(
        sa.case(
            (act_links.c.from_act_id == framework_act_id, act_links.c.to_act_id),
            else_=act_links.c.from_act_id,
        )
    ).where(
        sa.or_(
            act_links.c.from_act_id == framework_act_id,
            act_links.c.to_act_id == framework_act_id,
        ),
        act_links.c.link_type.in_(("EXECUTES", "RELATED_TO", "AWARDS")),
    )
    call_off_condition = procurement_acts.c.id.in_(linked_ids)
    if process_id is not None:
        call_off_condition = sa.or_(
            call_off_condition,
            sa.and_(
                procurement_acts.c.process_id == process_id,
                procurement_acts.c.agreement_type == "CALL_OFF",
            ),
        )
    call_offs = (
        await conn.execute(
            sa.select(
                sa.func.count(sa.distinct(procurement_acts.c.id)).label("count"),
                sa.func.coalesce(
                    sa.func.sum(
                        sa.func.coalesce(
                            procurement_acts.c.amount_gross,
                            procurement_acts.c.amount_net,
                            0,
                        )
                    ),
                    0,
                ).label("spend"),
            ).where(
                procurement_acts.c.id != framework_act_id,
                procurement_acts.c.is_current.is_(True),
                call_off_condition,
            )
        )
    ).one()
    buyer_count = (
        await conn.execute(
            sa.select(sa.func.count(sa.distinct(act_parties.c.entity_id)))
            .select_from(
                act_parties.join(procurement_acts, procurement_acts.c.id == act_parties.c.act_id)
            )
            .where(
                procurement_acts.c.id != framework_act_id,
                call_off_condition,
                act_parties.c.party_role.in_(("BUYER", "CONTRACTING_AUTHORITY")),
            )
        )
    ).scalar_one()
    return int(call_offs.count), Decimal(call_offs.spend or 0), int(buyer_count)


@router.get("", response_model=FrameworkListResponse)
async def list_frameworks(
    cpv_prefixes: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    excluded_cpv_prefixes: str | None = Query(default=None),
    nuts_code: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=60, ge=1, le=200),
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> FrameworkListResponse:
    cpvs = _csv_values(cpv_prefixes)
    excluded_cpvs = _csv_values(excluded_cpv_prefixes)
    terms = _csv_values(keywords)
    buyer = entities.alias("framework_buyer")
    query = (
        sa.select(
            procurement_acts,
            procurement_processes.c.public_id,
            procurement_processes.c.title.label("process_title"),
            procurement_processes.c.buyer_entity_id,
            buyer.c.canonical_name.label("buyer_name"),
            framework_watches.c.id.label("watch_id"),
            framework_watches.c.notify_before_days,
        )
        .outerjoin(procurement_processes, procurement_processes.c.id == procurement_acts.c.process_id)
        .outerjoin(buyer, buyer.c.id == procurement_processes.c.buyer_entity_id)
        .outerjoin(
            framework_watches,
            sa.and_(
                framework_watches.c.framework_act_id == procurement_acts.c.id,
                framework_watches.c.tenant_id == tenant_uuid(user),
            ),
        )
        .where(
            procurement_acts.c.agreement_type == "FRAMEWORK_AGREEMENT",
            procurement_acts.c.is_current.is_(True),
        )
    )
    if cpvs:
        query = query.where(
            sa.exists(
                sa.select(1).where(
                    act_cpv_codes.c.act_id == procurement_acts.c.id,
                    sa.or_(*(act_cpv_codes.c.cpv_code.like(f"{prefix}%") for prefix in cpvs)),
                )
            )
        )
    if excluded_cpvs:
        query = query.where(
            ~sa.exists(
                sa.select(1).where(
                    act_cpv_codes.c.act_id == procurement_acts.c.id,
                    sa.or_(*(act_cpv_codes.c.cpv_code.like(f"{prefix}%") for prefix in excluded_cpvs)),
                )
            )
        )
    if terms:
        query = query.where(
            sa.or_(
                *(
                    sa.func.unaccent(
                        sa.func.lower(
                            sa.func.coalesce(procurement_acts.c.title, procurement_processes.c.title, "")
                        )
                    ).like(sa.func.unaccent(f"%{term.casefold()}%"))
                    for term in terms
                )
            )
        )
    if nuts_code:
        query = query.where(
            sa.exists(
                sa.select(1).where(
                    act_locations.c.act_id == procurement_acts.c.id,
                    act_locations.c.nuts_code.like(f"{nuts_code}%"),
                )
            )
        )
    if date_from:
        query = query.where(procurement_acts.c.publication_date >= date_from)
    if date_to:
        query = query.where(procurement_acts.c.publication_date <= date_to)

    rows = (
        await conn.execute(
            query.order_by(
                procurement_acts.c.end_date.asc().nulls_last(),
                procurement_acts.c.publication_date.desc().nulls_last(),
            ).limit(limit)
        )
    ).all()
    results: list[FrameworkResponse] = []
    today = datetime.now(timezone.utc).date()
    for row in rows:
        cpv_codes = list(
            (
                await conn.execute(
                    sa.select(act_cpv_codes.c.cpv_code).where(
                        act_cpv_codes.c.act_id == row.id
                    )
                )
            ).scalars()
        )
        suppliers = await _framework_suppliers(conn, row.id)
        call_off_count, realized_spend, buyer_count = await _call_offs(
            conn,
            framework_act_id=row.id,
            process_id=row.process_id,
        )
        identifier = (
            await conn.execute(
                sa.select(act_identifiers.c.scheme, act_identifiers.c.value_raw)
                .where(
                    act_identifiers.c.act_id == row.id,
                    act_identifiers.c.scheme.in_(("ADAM", "ADA", "TED_NOTICE_ID")),
                )
                .order_by(
                    sa.case(
                        (act_identifiers.c.scheme == "ADAM", 1),
                        (act_identifiers.c.scheme == "ADA", 2),
                        else_=3,
                    )
                )
                .limit(1)
            )
        ).first()
        official_url = None
        official_identifier = None
        if identifier:
            official_identifier = identifier.value_raw
            official_url, _ = links_for_display_identifier(identifier.scheme, identifier.value_raw)
        status = framework_window_status(row.end_date, today=today)
        ceiling = row.framework_ceiling_amount
        utilization = (
            round(float(realized_spend / ceiling), 4) if ceiling and ceiling > 0 else None
        )
        results.append(
            FrameworkResponse(
                act_id=str(row.id),
                process_id=str(row.process_id) if row.process_id else None,
                public_id=row.public_id,
                title=row.title or row.process_title or row.public_id or "Framework agreement",
                buyer_id=str(row.buyer_entity_id) if row.buyer_entity_id else None,
                buyer_name=row.buyer_name,
                cpv_codes=cpv_codes,
                status=status,
                publication_date=row.publication_date,
                valid_from=row.start_date,
                valid_until=row.end_date,
                days_to_expiry=(row.end_date - today).days if row.end_date else None,
                ceiling_amount=ceiling,
                realized_spend=realized_spend,
                utilization=utilization,
                call_off_count=call_off_count,
                suppliers=suppliers,
                buyer_count=max(buyer_count, 1 if row.buyer_entity_id else 0),
                relevance_score=framework_relevance_score(
                    cpv_codes=cpv_codes,
                    profile_cpv_prefixes=cpvs,
                    realized_spend=realized_spend,
                    ceiling_amount=ceiling,
                    status=status,
                ),
                official_identifier=official_identifier,
                official_url=official_url,
                watched=row.watch_id is not None,
                watch_id=str(row.watch_id) if row.watch_id else None,
                notify_before_days=row.notify_before_days,
            )
        )
    results.sort(key=lambda item: (item.relevance_score, item.realized_spend), reverse=True)
    return FrameworkListResponse(
        generated_at=datetime.now(timezone.utc),
        summary={
            "framework_count": len(results),
            "active_count": sum(item.status == "ACTIVE" for item in results),
            "reopening_count": sum(item.status == "REOPENING" for item in results),
            "realized_spend": str(sum((item.realized_spend for item in results), Decimal(0))),
            "ceiling_value": str(
                sum((item.ceiling_amount or Decimal(0) for item in results), Decimal(0))
            ),
            "supplier_count": len(
                {supplier.entity_id for item in results for supplier in item.suppliers}
            ),
        },
        frameworks=results,
        methodology=[
            "Framework ceiling is shown separately and is never counted as realized public spend.",
            "Realized spend includes explicit CALL_OFF acts and officially linked execution/award acts.",
            "Reopening indicates an agreement expiring within 180 days; confirm renewal dates in official evidence.",
        ],
    )


@router.post("/{framework_act_id}/watch", status_code=201)
async def watch_framework(
    framework_act_id: str,
    body: FrameworkWatchRequest,
    user: AuthenticatedUser = Depends(require_role(*_EDIT_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> dict[str, str | int]:
    target = parse_uuid_or_422(framework_act_id, label="framework act id")
    exists = (
        await conn.execute(
            sa.select(procurement_acts.c.id).where(
                procurement_acts.c.id == target,
                procurement_acts.c.agreement_type == "FRAMEWORK_AGREEMENT",
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Framework agreement not found")
    workspace_user_id = await ensure_workspace_user(conn, user)
    statement = pg_insert(framework_watches).values(
        id=uuid.uuid4(),
        tenant_id=tenant_uuid(user),
        user_id=workspace_user_id,
        framework_act_id=target,
        notify_before_days=body.notify_before_days,
    )
    await conn.execute(
        statement.on_conflict_do_update(
            index_elements=[
                framework_watches.c.tenant_id,
                framework_watches.c.framework_act_id,
            ],
            set_={
                "notify_before_days": statement.excluded.notify_before_days,
                "user_id": workspace_user_id,
            },
        )
    )
    return {"framework_act_id": str(target), "notify_before_days": body.notify_before_days}


@router.delete("/{framework_act_id}/watch", status_code=204)
async def unwatch_framework(
    framework_act_id: str,
    user: AuthenticatedUser = Depends(require_role(*_EDIT_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> Response:
    target = parse_uuid_or_422(framework_act_id, label="framework act id")
    result = await conn.execute(
        framework_watches.delete().where(
            framework_watches.c.tenant_id == tenant_uuid(user),
            framework_watches.c.framework_act_id == target,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Framework watch not found")
    return Response(status_code=204)

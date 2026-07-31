"""Officially sourced buyer stakeholders and tenant watchlists."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    decision_makers,
    decision_maker_watches,
    diavgeia_decision_versions,
    entities,
)

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..queries import parse_uuid_or_422
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(tags=["decision-makers"])
_EDIT_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")


class DecisionInvolvementResponse(BaseModel):
    official_identifier: str | None
    event_date: datetime | None
    official_url: str | None


class DecisionMakerResponse(BaseModel):
    id: str
    buyer_entity_id: str
    full_name: str
    job_title: str | None
    department: str | None
    decision_role: str
    email: str | None
    phone: str | None
    profile_url: str | None
    source_system: str
    source_url: str | None
    legal_basis: str
    confidence: float
    is_current: bool
    observed_at: datetime
    watched: bool
    watch_id: str | None
    watch_notes: str | None
    recent_involvement: list[DecisionInvolvementResponse]


class DecisionMakerListResponse(BaseModel):
    buyer_id: str
    stakeholders: list[DecisionMakerResponse]
    methodology: str


class WatchRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


async def _serialize(
    conn: AsyncConnection,
    row: Any,
) -> DecisionMakerResponse:
    recent: list[DecisionInvolvementResponse] = []
    if row.source_system == "DIAVGEIA" and row.source_identifier:
        involvement_rows = (
            await conn.execute(
                sa.select(
                    diavgeia_decision_versions.c.ada,
                    diavgeia_decision_versions.c.issue_date,
                    diavgeia_decision_versions.c.document_url,
                )
                .where(
                    diavgeia_decision_versions.c.signer_uids.any(row.source_identifier)
                )
                .order_by(
                    diavgeia_decision_versions.c.issue_date.desc().nulls_last(),
                    diavgeia_decision_versions.c.observed_at.desc(),
                )
                .limit(5)
            )
        ).all()
        recent = [
            DecisionInvolvementResponse(
                official_identifier=item.ada,
                event_date=item.issue_date,
                official_url=item.document_url
                or (
                    f"https://diavgeia.gov.gr/decision/view/{item.ada}"
                    if item.ada
                    else None
                ),
            )
            for item in involvement_rows
        ]
    return DecisionMakerResponse(
        id=str(row.id),
        buyer_entity_id=str(row.buyer_entity_id),
        full_name=row.full_name,
        job_title=row.job_title,
        department=row.department,
        decision_role=row.decision_role,
        email=row.email,
        phone=row.phone,
        profile_url=row.profile_url,
        source_system=row.source_system,
        source_url=row.source_url,
        legal_basis=row.legal_basis,
        confidence=float(row.confidence),
        is_current=row.is_current,
        observed_at=row.observed_at,
        watched=row.watch_id is not None,
        watch_id=str(row.watch_id) if row.watch_id else None,
        watch_notes=row.watch_notes,
        recent_involvement=recent,
    )


def _stakeholder_query(tenant_id: uuid.UUID) -> sa.Select:
    return (
        sa.select(
            decision_makers,
            decision_maker_watches.c.id.label("watch_id"),
            decision_maker_watches.c.notes.label("watch_notes"),
        )
        .outerjoin(
            decision_maker_watches,
            sa.and_(
                decision_maker_watches.c.decision_maker_id == decision_makers.c.id,
                decision_maker_watches.c.tenant_id == tenant_id,
            ),
        )
    )


@router.get(
    "/v1/buyers/{buyer_id}/decision-makers",
    response_model=DecisionMakerListResponse,
)
async def list_buyer_decision_makers(
    buyer_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> DecisionMakerListResponse:
    buyer_uuid = parse_uuid_or_422(buyer_id, label="buyer id")
    exists = (
        await conn.execute(sa.select(entities.c.id).where(entities.c.id == buyer_uuid))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    rows = (
        await conn.execute(
            _stakeholder_query(tenant_uuid(user))
            .where(decision_makers.c.buyer_entity_id == buyer_uuid)
            .order_by(
                decision_makers.c.is_current.desc(),
                decision_makers.c.confidence.desc(),
                decision_makers.c.full_name,
            )
        )
    ).all()
    return DecisionMakerListResponse(
        buyer_id=buyer_id,
        stakeholders=[await _serialize(conn, row) for row in rows],
        methodology=(
            "Only people explicitly published by official Διαύγεια or ΑΝΑΠΤΥΞΗ "
            "records are shown. Role labels reflect published titles; they are not "
            "predictions of purchasing authority."
        ),
    )


@router.get("/v1/decision-makers/watches", response_model=list[DecisionMakerResponse])
async def list_decision_maker_watches(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> list[DecisionMakerResponse]:
    rows = (
        await conn.execute(
            _stakeholder_query(tenant_uuid(user))
            .where(decision_maker_watches.c.id.is_not(None))
            .order_by(decision_maker_watches.c.created_at.desc())
        )
    ).all()
    return [await _serialize(conn, row) for row in rows]


@router.post(
    "/v1/decision-makers/{decision_maker_id}/watch",
    response_model=DecisionMakerResponse,
)
async def watch_decision_maker(
    decision_maker_id: str,
    body: WatchRequest,
    user: AuthenticatedUser = Depends(require_role(*_EDIT_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> DecisionMakerResponse:
    target = parse_uuid_or_422(decision_maker_id, label="decision maker id")
    exists = (
        await conn.execute(
            sa.select(decision_makers.c.id).where(decision_makers.c.id == target)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Decision maker not found")
    workspace_user_id = await ensure_workspace_user(conn, user)
    statement = pg_insert(decision_maker_watches).values(
        id=uuid.uuid4(),
        tenant_id=tenant_uuid(user),
        user_id=workspace_user_id,
        decision_maker_id=target,
        notes=body.notes,
    )
    await conn.execute(
        statement.on_conflict_do_update(
            index_elements=[
                decision_maker_watches.c.tenant_id,
                decision_maker_watches.c.decision_maker_id,
            ],
            set_={"notes": statement.excluded.notes, "user_id": workspace_user_id},
        )
    )
    row = (
        await conn.execute(
            _stakeholder_query(tenant_uuid(user)).where(decision_makers.c.id == target)
        )
    ).one()
    return await _serialize(conn, row)


@router.delete("/v1/decision-makers/{decision_maker_id}/watch", status_code=204)
async def unwatch_decision_maker(
    decision_maker_id: str,
    user: AuthenticatedUser = Depends(require_role(*_EDIT_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> Response:
    target = parse_uuid_or_422(decision_maker_id, label="decision maker id")
    result = await conn.execute(
        decision_maker_watches.delete().where(
            decision_maker_watches.c.tenant_id == tenant_uuid(user),
            decision_maker_watches.c.decision_maker_id == target,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Watch not found")
    return Response(status_code=204)

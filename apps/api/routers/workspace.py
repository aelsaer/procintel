"""Authenticated commercial workspace workflows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    audit_log,
    entities,
    notes,
    opportunity_pipeline_history,
    opportunity_pipeline_items,
    opportunity_scores,
    procurement_processes,
    saved_searches,
    tag_links,
    tags,
    tenants,
    workspace_watch_items,
)

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/workspace", tags=["workspace"])
_WRITE_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")
_PIPELINE_STAGES = {"WATCHING", "QUALIFYING", "BID_NO_BID", "BIDDING", "WON", "LOST", "DROPPED"}
_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT"}


class MeResponse(BaseModel):
    subject: str
    email: str | None
    tenant_id: str
    tenant_name: str
    plan: str
    role: str


class LoginAckResponse(BaseModel):
    acknowledged: bool = True


class SavedSearchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    query: dict[str, Any]


class SavedSearchResponse(SavedSearchRequest):
    id: str
    created_at: datetime
    updated_at: datetime


class PipelineCreateRequest(BaseModel):
    process_id: str
    stage: str = "WATCHING"
    priority: str = "MEDIUM"
    expected_value: Decimal | None = Field(default=None, ge=0)
    next_action: str | None = Field(default=None, max_length=1000)
    due_at: datetime | None = None


class PipelineUpdateRequest(BaseModel):
    stage: str | None = None
    priority: str | None = None
    expected_value: Decimal | None = Field(default=None, ge=0)
    next_action: str | None = Field(default=None, max_length=1000)
    due_at: datetime | None = None


class PipelineItemResponse(BaseModel):
    id: str
    process_id: str
    process_title: str | None
    stage: str
    priority: str
    expected_value: Decimal | None
    next_action: str | None
    due_at: datetime | None
    opportunity_score: Decimal | None
    added_at: datetime
    updated_at: datetime


class NoteRequest(BaseModel):
    object_type: str = Field(min_length=1, max_length=80)
    object_id: str
    body: str = Field(min_length=1, max_length=20_000)


class NoteResponse(BaseModel):
    id: str
    object_type: str
    object_id: str
    body: str
    created_at: datetime
    updated_at: datetime


class NoteUpdateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


class TagRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TagLinkRequest(BaseModel):
    object_type: str
    object_id: str


class TagResponse(BaseModel):
    id: str
    name: str
    linked_count: int = 0


class WatchRequest(BaseModel):
    object_type: Literal["BUYER", "COMPETITOR", "SUPPLIER"]
    object_id: str


class WatchResponse(BaseModel):
    id: str
    object_type: str
    object_id: str
    object_name: str | None
    created_at: datetime


async def _audit(
    conn: AsyncConnection, *, tenant_id: uuid.UUID, user_id: uuid.UUID,
    action: str, object_type: str, object_id: uuid.UUID, details: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        audit_log.insert().values(
            id=uuid.uuid4(), tenant_id=tenant_id, actor_user_id=user_id,
            action=action, object_type=object_type, object_id=object_id,
            details=jsonable_encoder(details or {}),
        )
    )


def _uuid(value: str, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} is not a valid UUID") from exc


@router.get("/me", response_model=MeResponse)
async def get_me(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> MeResponse:
    await ensure_workspace_user(conn, user)
    tenant = (await conn.execute(sa.select(tenants).where(tenants.c.id == tenant_uuid(user)))).one()
    return MeResponse(
        subject=user.subject, email=user.email, tenant_id=str(tenant.id),
        tenant_name=tenant.name, plan=tenant.plan, role=user.role,
    )


@router.post("/login", response_model=LoginAckResponse, status_code=201)
async def acknowledge_login(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> LoginAckResponse:
    """§40.3 names "login" explicitly as an audited action. The frontend
    calls this once, from `procurementAuthProvider.login()`
    (`apps/web/src/lib/auth-provider.ts`) right after a successful
    `/me` check that follows the OIDC authorization-code exchange (see
    `apps/web/src/app/callback/page.tsx`) — deliberately not folded into
    `/me` itself, which is also called for routine identity/session
    checks and would otherwise flood the audit log with one row per
    poll/page-load rather than one per actual sign-in."""
    user_id = await ensure_workspace_user(conn, user)
    await _audit(conn, tenant_id=tenant_uuid(user), user_id=user_id, action="login", object_type="users", object_id=user_id)
    return LoginAckResponse()


def _saved(row: Any) -> SavedSearchResponse:
    return SavedSearchResponse(
        id=str(row.id), name=row.name, query=row.query,
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("/saved-searches", response_model=list[SavedSearchResponse])
async def list_saved_searches(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[SavedSearchResponse]:
    rows = (await conn.execute(
        sa.select(saved_searches).where(saved_searches.c.tenant_id == tenant_uuid(user)).order_by(saved_searches.c.updated_at.desc())
    )).all()
    return [_saved(row) for row in rows]


@router.post("/saved-searches", response_model=SavedSearchResponse, status_code=201)
async def create_saved_search(
    body: SavedSearchRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> SavedSearchResponse:
    user_id = await ensure_workspace_user(conn, user)
    item_id = uuid.uuid4()
    await conn.execute(saved_searches.insert().values(
        id=item_id, tenant_id=tenant_uuid(user), user_id=user_id, name=body.name, query=body.query,
    ))
    await _audit(conn, tenant_id=tenant_uuid(user), user_id=user_id, action="saved_search.created", object_type="saved_search", object_id=item_id)
    row = (await conn.execute(sa.select(saved_searches).where(saved_searches.c.id == item_id))).one()
    return _saved(row)


@router.put("/saved-searches/{item_id}", response_model=SavedSearchResponse)
async def update_saved_search(
    item_id: str, body: SavedSearchRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> SavedSearchResponse:
    target = _uuid(item_id)
    user_id = await ensure_workspace_user(conn, user)
    result = await conn.execute(
        saved_searches.update().where(saved_searches.c.id == target, saved_searches.c.tenant_id == tenant_uuid(user))
        .values(name=body.name, query=body.query, updated_at=datetime.now(timezone.utc))
        .returning(saved_searches)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    await _audit(conn, tenant_id=tenant_uuid(user), user_id=user_id, action="saved_search.updated", object_type="saved_search", object_id=target)
    return _saved(row)


@router.delete("/saved-searches/{item_id}", status_code=204)
async def delete_saved_search(
    item_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> Response:
    target = _uuid(item_id)
    user_id = await ensure_workspace_user(conn, user)
    result = await conn.execute(saved_searches.delete().where(saved_searches.c.id == target, saved_searches.c.tenant_id == tenant_uuid(user)))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Saved search not found")
    await _audit(conn, tenant_id=tenant_uuid(user), user_id=user_id, action="saved_search.deleted", object_type="saved_search", object_id=target)
    return Response(status_code=204)


def _validate_pipeline(stage: str, priority: str) -> None:
    if stage not in _PIPELINE_STAGES:
        raise HTTPException(status_code=422, detail=f"Unknown pipeline stage: {stage}")
    if priority not in _PRIORITIES:
        raise HTTPException(status_code=422, detail=f"Unknown priority: {priority}")


def _pipeline(row: Any) -> PipelineItemResponse:
    return PipelineItemResponse(
        id=str(row.id), process_id=str(row.process_id), process_title=row.process_title,
        stage=row.stage, priority=row.priority, expected_value=row.expected_value,
        next_action=row.next_action, due_at=row.due_at, opportunity_score=row.opportunity_score,
        added_at=row.added_at, updated_at=row.updated_at,
    )


async def _pipeline_query(conn: AsyncConnection, tenant_id: uuid.UUID, item_id: uuid.UUID | None = None) -> list[Any]:
    query = (
        sa.select(
            opportunity_pipeline_items,
            procurement_processes.c.title.label("process_title"),
            opportunity_scores.c.total_score.label("opportunity_score"),
        )
        .join(procurement_processes, procurement_processes.c.id == opportunity_pipeline_items.c.process_id)
        .outerjoin(
            opportunity_scores,
            sa.and_(
                opportunity_scores.c.process_id == opportunity_pipeline_items.c.process_id,
                opportunity_scores.c.tenant_id == opportunity_pipeline_items.c.tenant_id,
            ),
        )
        .where(opportunity_pipeline_items.c.tenant_id == tenant_id)
        .order_by(opportunity_pipeline_items.c.updated_at.desc())
    )
    if item_id:
        query = query.where(opportunity_pipeline_items.c.id == item_id)
    return (await conn.execute(query)).all()


@router.get("/pipeline", response_model=list[PipelineItemResponse])
async def list_pipeline(
    stage: str | None = Query(default=None),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[PipelineItemResponse]:
    rows = await _pipeline_query(conn, tenant_uuid(user))
    return [_pipeline(row) for row in rows if stage is None or row.stage == stage]


@router.post("/pipeline", response_model=PipelineItemResponse, status_code=201)
async def create_pipeline_item(
    body: PipelineCreateRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> PipelineItemResponse:
    _validate_pipeline(body.stage, body.priority)
    process_id = _uuid(body.process_id, "process_id")
    if not (await conn.execute(sa.select(procurement_processes.c.id).where(procurement_processes.c.id == process_id))).first():
        raise HTTPException(status_code=404, detail="Procurement process not found")
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    item_id = uuid.uuid4()
    await conn.execute(
        pg_insert(opportunity_pipeline_items).values(
            id=item_id, tenant_id=tenant_id, user_id=user_id, process_id=process_id,
            stage=body.stage, priority=body.priority, expected_value=body.expected_value,
            next_action=body.next_action, due_at=body.due_at, assigned_user_id=user_id,
        ).on_conflict_do_update(
            index_elements=[opportunity_pipeline_items.c.tenant_id, opportunity_pipeline_items.c.process_id, opportunity_pipeline_items.c.user_id],
            set_={"stage": body.stage, "priority": body.priority, "expected_value": body.expected_value,
                  "next_action": body.next_action, "due_at": body.due_at, "updated_at": datetime.now(timezone.utc)},
        )
    )
    actual_id = (await conn.execute(sa.select(opportunity_pipeline_items.c.id).where(
        opportunity_pipeline_items.c.tenant_id == tenant_id,
        opportunity_pipeline_items.c.process_id == process_id,
        opportunity_pipeline_items.c.user_id == user_id,
    ))).scalar_one()
    await conn.execute(opportunity_pipeline_history.insert().values(
        id=uuid.uuid4(), pipeline_item_id=actual_id, tenant_id=tenant_id,
        to_stage=body.stage, changed_by=user_id,
    ))
    await _audit(conn, tenant_id=tenant_id, user_id=user_id, action="pipeline.saved", object_type="pipeline_item", object_id=actual_id)
    return _pipeline((await _pipeline_query(conn, tenant_id, actual_id))[0])


@router.patch("/pipeline/{item_id}", response_model=PipelineItemResponse)
async def update_pipeline_item(
    item_id: str, body: PipelineUpdateRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> PipelineItemResponse:
    target = _uuid(item_id)
    tenant_id = tenant_uuid(user)
    current = (await conn.execute(sa.select(opportunity_pipeline_items).where(
        opportunity_pipeline_items.c.id == target, opportunity_pipeline_items.c.tenant_id == tenant_id,
    ))).first()
    if current is None:
        raise HTTPException(status_code=404, detail="Pipeline item not found")
    stage = body.stage or current.stage
    priority = body.priority or current.priority
    _validate_pipeline(stage, priority)
    values = {"stage": stage, "priority": priority, "updated_at": datetime.now(timezone.utc)}
    for field in ("expected_value", "next_action", "due_at"):
        value = getattr(body, field)
        if value is not None:
            values[field] = value
    await conn.execute(opportunity_pipeline_items.update().where(opportunity_pipeline_items.c.id == target).values(**values))
    user_id = await ensure_workspace_user(conn, user)
    if stage != current.stage:
        await conn.execute(opportunity_pipeline_history.insert().values(
            id=uuid.uuid4(), pipeline_item_id=target, tenant_id=tenant_id,
            from_stage=current.stage, to_stage=stage, changed_by=user_id,
        ))
    await _audit(conn, tenant_id=tenant_id, user_id=user_id, action="pipeline.updated", object_type="pipeline_item", object_id=target, details=values)
    return _pipeline((await _pipeline_query(conn, tenant_id, target))[0])


@router.delete("/pipeline/{item_id}", status_code=204)
async def delete_pipeline_item(
    item_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> Response:
    target = _uuid(item_id)
    result = await conn.execute(opportunity_pipeline_items.delete().where(
        opportunity_pipeline_items.c.id == target, opportunity_pipeline_items.c.tenant_id == tenant_uuid(user),
    ))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Pipeline item not found")
    return Response(status_code=204)


def _note(row: Any) -> NoteResponse:
    return NoteResponse(id=str(row.id), object_type=row.object_type, object_id=str(row.object_id), body=row.body, created_at=row.created_at, updated_at=row.updated_at)


@router.get("/notes", response_model=list[NoteResponse])
async def list_notes(
    object_type: str, object_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[NoteResponse]:
    rows = (await conn.execute(sa.select(notes).where(
        notes.c.tenant_id == tenant_uuid(user), notes.c.object_type == object_type,
        notes.c.object_id == _uuid(object_id, "object_id"),
    ).order_by(notes.c.updated_at.desc()))).all()
    return [_note(row) for row in rows]


@router.post("/notes", response_model=NoteResponse, status_code=201)
async def create_note(
    body: NoteRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> NoteResponse:
    user_id = await ensure_workspace_user(conn, user)
    note_id = uuid.uuid4()
    await conn.execute(notes.insert().values(
        id=note_id, tenant_id=tenant_uuid(user), user_id=user_id,
        object_type=body.object_type, object_id=_uuid(body.object_id, "object_id"), body=body.body,
    ))
    row = (await conn.execute(sa.select(notes).where(notes.c.id == note_id))).one()
    return _note(row)


@router.patch("/notes/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str, body: NoteUpdateRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> NoteResponse:
    row = (await conn.execute(notes.update().where(
        notes.c.id == _uuid(note_id), notes.c.tenant_id == tenant_uuid(user),
    ).values(body=body.body, updated_at=datetime.now(timezone.utc)).returning(notes))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return _note(row)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(
    note_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> Response:
    result = await conn.execute(notes.delete().where(notes.c.id == _uuid(note_id), notes.c.tenant_id == tenant_uuid(user)))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Note not found")
    return Response(status_code=204)


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[TagResponse]:
    rows = (await conn.execute(
        sa.select(tags.c.id, tags.c.name, sa.func.count(tag_links.c.object_id).label("linked_count"))
        .outerjoin(tag_links, tag_links.c.tag_id == tags.c.id)
        .where(tags.c.tenant_id == tenant_uuid(user)).group_by(tags.c.id).order_by(tags.c.name)
    )).all()
    return [TagResponse(id=str(row.id), name=row.name, linked_count=row.linked_count) for row in rows]


@router.post("/tags", response_model=TagResponse, status_code=201)
async def create_tag(
    body: TagRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> TagResponse:
    await ensure_workspace_user(conn, user)
    tag_id = uuid.uuid4()
    await conn.execute(pg_insert(tags).values(id=tag_id, tenant_id=tenant_uuid(user), name=body.name.strip()).on_conflict_do_nothing())
    row = (await conn.execute(sa.select(tags).where(tags.c.tenant_id == tenant_uuid(user), tags.c.name == body.name.strip()))).one()
    return TagResponse(id=str(row.id), name=row.name)


@router.get("/tags/links", response_model=list[TagResponse])
async def list_object_tags(
    object_type: str, object_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[TagResponse]:
    rows = (await conn.execute(
        sa.select(tags.c.id, tags.c.name)
        .join(tag_links, tag_links.c.tag_id == tags.c.id)
        .where(
            tags.c.tenant_id == tenant_uuid(user), tag_links.c.object_type == object_type,
            tag_links.c.object_id == _uuid(object_id, "object_id"),
        ).order_by(tags.c.name)
    )).all()
    return [TagResponse(id=str(row.id), name=row.name, linked_count=1) for row in rows]


@router.post("/tags/{tag_id}/links", status_code=204)
async def link_tag(
    tag_id: str, body: TagLinkRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> Response:
    target = _uuid(tag_id)
    if not (await conn.execute(sa.select(tags.c.id).where(tags.c.id == target, tags.c.tenant_id == tenant_uuid(user)))).first():
        raise HTTPException(status_code=404, detail="Tag not found")
    await conn.execute(pg_insert(tag_links).values(
        tag_id=target, object_type=body.object_type, object_id=_uuid(body.object_id, "object_id"),
    ).on_conflict_do_nothing())
    return Response(status_code=204)


@router.delete("/tags/{tag_id}/links/{object_type}/{object_id}", status_code=204)
async def unlink_tag(
    tag_id: str, object_type: str, object_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> Response:
    target_tag = _uuid(tag_id)
    if not (await conn.execute(sa.select(tags.c.id).where(
        tags.c.id == target_tag, tags.c.tenant_id == tenant_uuid(user),
    ))).first():
        raise HTTPException(status_code=404, detail="Tag not found")
    await conn.execute(tag_links.delete().where(
        tag_links.c.tag_id == target_tag, tag_links.c.object_type == object_type,
        tag_links.c.object_id == _uuid(object_id, "object_id"),
    ))
    return Response(status_code=204)


def _watch(row: Any) -> WatchResponse:
    return WatchResponse(id=str(row.id), object_type=row.object_type, object_id=str(row.object_id), object_name=row.object_name, created_at=row.created_at)


@router.get("/watches", response_model=list[WatchResponse])
async def list_watches(
    object_type: str | None = None,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[WatchResponse]:
    query = sa.select(workspace_watch_items, entities.c.canonical_name.label("object_name")).outerjoin(
        entities, entities.c.id == workspace_watch_items.c.object_id,
    ).where(workspace_watch_items.c.tenant_id == tenant_uuid(user)).order_by(workspace_watch_items.c.created_at.desc())
    if object_type:
        query = query.where(workspace_watch_items.c.object_type == object_type)
    return [_watch(row) for row in (await conn.execute(query)).all()]


@router.post("/watches", response_model=WatchResponse, status_code=201)
async def create_watch(
    body: WatchRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> WatchResponse:
    object_id = _uuid(body.object_id, "object_id")
    if not (await conn.execute(sa.select(entities.c.id).where(entities.c.id == object_id))).first():
        raise HTTPException(status_code=404, detail="Entity not found")
    user_id = await ensure_workspace_user(conn, user)
    await conn.execute(pg_insert(workspace_watch_items).values(
        id=uuid.uuid4(), tenant_id=tenant_uuid(user), user_id=user_id,
        object_type=body.object_type, object_id=object_id,
    ).on_conflict_do_nothing())
    row = (await conn.execute(
        sa.select(workspace_watch_items, entities.c.canonical_name.label("object_name"))
        .join(entities, entities.c.id == workspace_watch_items.c.object_id)
        .where(workspace_watch_items.c.tenant_id == tenant_uuid(user), workspace_watch_items.c.object_type == body.object_type, workspace_watch_items.c.object_id == object_id)
    )).one()
    return _watch(row)


@router.delete("/watches/{watch_id}", status_code=204)
async def delete_watch(
    watch_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> Response:
    result = await conn.execute(workspace_watch_items.delete().where(
        workspace_watch_items.c.id == _uuid(watch_id), workspace_watch_items.c.tenant_id == tenant_uuid(user),
    ))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Watch not found")
    return Response(status_code=204)

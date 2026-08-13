"""Tenant-scoped bid qualification and execution workspace."""

from __future__ import annotations

import os
import uuid
from datetime import date
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    audit_log,
    bid_certificate_links,
    bid_comments,
    bid_reminders,
    bid_requirements,
    bid_tasks,
    bid_workspaces,
    crm_handoffs,
    document_act_links,
    documents,
    procurement_acts,
    procurement_processes,
    tenant_certificates,
    tenant_memberships,
    users,
)

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..deps import get_http_client
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/bids", tags=["bids"])
_WRITE_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")
_WORKSPACE_STATUSES = {
    "QUALIFYING",
    "PREPARING",
    "REVIEW",
    "SUBMITTED",
    "WON",
    "LOST",
    "ARCHIVED",
}
_DECISIONS = {"PENDING", "BID", "NO_BID", "CONDITIONAL"}
_TASK_STATUSES = {"TODO", "IN_PROGRESS", "BLOCKED", "DONE"}
_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT"}
_REQUIREMENT_TYPES = {
    "ELIGIBILITY",
    "TECHNICAL",
    "FINANCIAL",
    "CERTIFICATE",
    "DELIVERABLE",
    "DEADLINE",
    "LEGAL",
    "OTHER",
}
_REQUIREMENT_STATUSES = {"UNREVIEWED", "MET", "PARTIAL", "MISSING", "NOT_APPLICABLE"}


class BidTaskResponse(BaseModel):
    id: str
    title: str
    description: str | None = None
    assigned_user_id: str | None = None
    status: str
    priority: str
    due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BidRequirementResponse(BaseModel):
    id: str
    requirement_type: str
    title: str
    description: str | None = None
    status: str
    mandatory: bool
    evidence_document_id: str | None = None
    evidence_page: int | None = None
    source_excerpt: str | None = None
    owner_user_id: str | None = None
    due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BidWorkspaceResponse(BaseModel):
    id: str
    process_id: str
    process_title: str | None = None
    owner_user_id: str | None = None
    status: str
    decision: str
    decision_rationale: str | None = None
    decision_by: str | None = None
    decision_at: datetime | None = None
    submission_due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    tasks: list[BidTaskResponse]
    requirements: list[BidRequirementResponse]
    comments: list[dict]
    reminders: list[dict]
    certificates: list[dict]
    crm_handoffs: list[dict]
    activity: list[dict]


class BidWorkspaceUpdate(BaseModel):
    owner_user_id: uuid.UUID | None = None
    status: str | None = None
    decision: str | None = None
    decision_rationale: str | None = Field(default=None, max_length=5000)
    submission_due_at: datetime | None = None


class BidTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    assigned_user_id: uuid.UUID | None = None
    status: str = "TODO"
    priority: str = "MEDIUM"
    due_at: datetime | None = None


class BidTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    assigned_user_id: uuid.UUID | None = None
    status: str | None = None
    priority: str | None = None
    due_at: datetime | None = None


class BidRequirementCreate(BaseModel):
    requirement_type: str = "OTHER"
    title: str = Field(min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=10_000)
    status: str = "UNREVIEWED"
    mandatory: bool = True
    evidence_document_id: uuid.UUID | None = None
    evidence_page: int | None = Field(default=None, ge=1)
    source_excerpt: str | None = Field(default=None, max_length=5000)
    owner_user_id: uuid.UUID | None = None
    due_at: datetime | None = None


class BidRequirementUpdate(BaseModel):
    requirement_type: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=10_000)
    status: str | None = None
    mandatory: bool | None = None
    evidence_document_id: uuid.UUID | None = None
    evidence_page: int | None = Field(default=None, ge=1)
    source_excerpt: str | None = Field(default=None, max_length=5000)
    owner_user_id: uuid.UUID | None = None
    due_at: datetime | None = None


class BidCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    task_id: uuid.UUID | None = None
    requirement_id: uuid.UUID | None = None


class BidCommentUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class BidReminderCreate(BaseModel):
    remind_at: datetime
    assigned_user_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    requirement_id: uuid.UUID | None = None
    channel: str = "IN_APP"


class BidReminderUpdate(BaseModel):
    remind_at: datetime | None = None
    assigned_user_id: uuid.UUID | None = None
    status: str | None = None
    channel: str | None = None


class TenantCertificateCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    certificate_type: str = Field(min_length=1, max_length=120)
    issuer: str | None = Field(default=None, max_length=500)
    reference_number: str | None = Field(default=None, max_length=250)
    file_name: str | None = Field(default=None, max_length=500)
    storage_uri: str | None = Field(default=None, max_length=2000)
    issued_at: date | None = None
    expires_at: date | None = None
    metadata: dict = Field(default_factory=dict)


class CertificateLinkCreate(BaseModel):
    certificate_id: uuid.UUID
    requirement_id: uuid.UUID | None = None


class CrmHandoffCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    payload: dict = Field(default_factory=dict)


def _task_response(row) -> BidTaskResponse:
    return BidTaskResponse(
        id=str(row.id),
        title=row.title,
        description=row.description,
        assigned_user_id=str(row.assigned_user_id) if row.assigned_user_id else None,
        status=row.status,
        priority=row.priority,
        due_at=row.due_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _requirement_response(row) -> BidRequirementResponse:
    return BidRequirementResponse(
        id=str(row.id),
        requirement_type=row.requirement_type,
        title=row.title,
        description=row.description,
        status=row.status,
        mandatory=row.mandatory,
        evidence_document_id=str(row.evidence_document_id) if row.evidence_document_id else None,
        evidence_page=row.evidence_page,
        source_excerpt=row.source_excerpt,
        owner_user_id=str(row.owner_user_id) if row.owner_user_id else None,
        due_at=row.due_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _serialize_workspace(
    conn: AsyncConnection,
    *,
    workspace_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> BidWorkspaceResponse:
    row = (
        await conn.execute(
            sa.select(
                bid_workspaces,
                procurement_processes.c.title.label("process_title"),
            )
            .join(procurement_processes, procurement_processes.c.id == bid_workspaces.c.process_id)
            .where(
                bid_workspaces.c.id == workspace_id,
                bid_workspaces.c.tenant_id == tenant_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="bid workspace not found")
    tasks = (
        await conn.execute(
            sa.select(bid_tasks)
            .where(
                bid_tasks.c.bid_workspace_id == workspace_id,
                bid_tasks.c.tenant_id == tenant_id,
            )
            .order_by(
                sa.case(
                    (bid_tasks.c.status == "BLOCKED", 0),
                    (bid_tasks.c.status == "IN_PROGRESS", 1),
                    (bid_tasks.c.status == "TODO", 2),
                    else_=3,
                ),
                bid_tasks.c.due_at.asc().nulls_last(),
                bid_tasks.c.created_at,
            )
        )
    ).all()
    requirements = (
        await conn.execute(
            sa.select(bid_requirements)
            .where(
                bid_requirements.c.bid_workspace_id == workspace_id,
                bid_requirements.c.tenant_id == tenant_id,
            )
            .order_by(
                bid_requirements.c.mandatory.desc(),
                bid_requirements.c.requirement_type,
                bid_requirements.c.created_at,
            )
        )
    ).all()
    comments = (
        await conn.execute(
            sa.select(
                bid_comments,
                users.c.display_name.label("author_name"),
                users.c.email.label("author_email"),
            )
            .join(users, users.c.id == bid_comments.c.author_user_id)
            .where(
                bid_comments.c.bid_workspace_id == workspace_id,
                bid_comments.c.tenant_id == tenant_id,
            )
            .order_by(bid_comments.c.created_at.desc())
        )
    ).mappings().all()
    reminders = (
        await conn.execute(
            sa.select(bid_reminders)
            .where(
                bid_reminders.c.bid_workspace_id == workspace_id,
                bid_reminders.c.tenant_id == tenant_id,
            )
            .order_by(bid_reminders.c.remind_at)
        )
    ).mappings().all()
    certificates = (
        await conn.execute(
            sa.select(
                bid_certificate_links.c.id.label("link_id"),
                bid_certificate_links.c.requirement_id,
                bid_certificate_links.c.linked_at,
                tenant_certificates,
            )
            .join(
                tenant_certificates,
                tenant_certificates.c.id == bid_certificate_links.c.certificate_id,
            )
            .where(
                bid_certificate_links.c.bid_workspace_id == workspace_id,
                bid_certificate_links.c.tenant_id == tenant_id,
            )
            .order_by(tenant_certificates.c.expires_at.asc().nulls_last())
        )
    ).mappings().all()
    handoffs = (
        await conn.execute(
            sa.select(crm_handoffs)
            .where(
                crm_handoffs.c.bid_workspace_id == workspace_id,
                crm_handoffs.c.tenant_id == tenant_id,
            )
            .order_by(crm_handoffs.c.created_at.desc())
        )
    ).mappings().all()
    activity = (
        await conn.execute(
            sa.select(audit_log)
            .where(
                audit_log.c.tenant_id == tenant_id,
                audit_log.c.object_type == "bid_workspace",
                audit_log.c.object_id == workspace_id,
            )
            .order_by(audit_log.c.created_at.desc())
            .limit(200)
        )
    ).mappings().all()
    return BidWorkspaceResponse(
        id=str(row.id),
        process_id=str(row.process_id),
        process_title=row.process_title,
        owner_user_id=str(row.owner_user_id) if row.owner_user_id else None,
        status=row.status,
        decision=row.decision,
        decision_rationale=row.decision_rationale,
        decision_by=str(row.decision_by) if row.decision_by else None,
        decision_at=row.decision_at,
        submission_due_at=row.submission_due_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        tasks=[_task_response(item) for item in tasks],
        requirements=[_requirement_response(item) for item in requirements],
        comments=[dict(item) for item in comments],
        reminders=[dict(item) for item in reminders],
        certificates=[dict(item) for item in certificates],
        crm_handoffs=[dict(item) for item in handoffs],
        activity=[dict(item) for item in activity],
    )


async def _workspace_id_for_process(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> uuid.UUID:
    workspace_id = (
        await conn.execute(
            sa.select(bid_workspaces.c.id).where(
                bid_workspaces.c.process_id == process_id,
                bid_workspaces.c.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if workspace_id is None:
        raise HTTPException(status_code=404, detail="bid workspace not found")
    return workspace_id


async def _audit(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    object_id: uuid.UUID,
    details: dict,
) -> None:
    await conn.execute(
        audit_log.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor_user_id=user_id,
            action=action,
            object_type="bid_workspace",
            object_id=object_id,
            details=jsonable_encoder(details),
        )
    )


async def _validate_tenant_user(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> None:
    if user_id is None:
        return
    exists = (
        await conn.execute(
            sa.select(tenant_memberships.c.user_id).where(
                tenant_memberships.c.tenant_id == tenant_id,
                tenant_memberships.c.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=422, detail="assigned user is not a member of this workspace")


@router.get("", response_model=list[BidWorkspaceResponse])
async def list_bid_workspaces(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[BidWorkspaceResponse]:
    tenant_id = tenant_uuid(user)
    ids = (
        await conn.execute(
            sa.select(bid_workspaces.c.id)
            .where(bid_workspaces.c.tenant_id == tenant_id)
            .order_by(bid_workspaces.c.updated_at.desc())
        )
    ).scalars().all()
    return [
        await _serialize_workspace(conn, workspace_id=workspace_id, tenant_id=tenant_id)
        for workspace_id in ids
    ]


@router.get("/certificates", response_model=list[dict])
async def list_tenant_certificates(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[dict]:
    tenant_id = tenant_uuid(user)
    rows = (
        await conn.execute(
            sa.select(tenant_certificates)
            .where(tenant_certificates.c.tenant_id == tenant_id)
            .order_by(
                tenant_certificates.c.expires_at.asc().nulls_last(),
                tenant_certificates.c.title,
            )
        )
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/certificates", response_model=dict, status_code=201)
async def create_tenant_certificate(
    body: TenantCertificateCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    row = (
        await conn.execute(
            tenant_certificates.insert()
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                created_by=user_id,
                **body.model_dump(),
            )
            .returning(tenant_certificates)
        )
    ).mappings().one()
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.certificate.created",
        object_id=row["id"],
        details={"certificate_type": row["certificate_type"]},
    )
    await conn.commit()
    return dict(row)


@router.patch("/certificates/{certificate_id}", response_model=dict)
async def update_tenant_certificate(
    certificate_id: uuid.UUID,
    body: TenantCertificateCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    row = (
        await conn.execute(
            tenant_certificates.update()
            .where(
                tenant_certificates.c.id == certificate_id,
                tenant_certificates.c.tenant_id == tenant_id,
            )
            .values(**body.model_dump(), updated_at=sa.func.now())
            .returning(tenant_certificates)
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="certificate not found")
    await conn.commit()
    return dict(row)


@router.delete("/certificates/{certificate_id}", status_code=204)
async def delete_tenant_certificate(
    certificate_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> None:
    result = await conn.execute(
        tenant_certificates.delete().where(
            tenant_certificates.c.id == certificate_id,
            tenant_certificates.c.tenant_id == tenant_uuid(user),
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="certificate not found")
    await conn.commit()


@router.get("/{process_id}", response_model=BidWorkspaceResponse)
async def get_bid_workspace(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> BidWorkspaceResponse:
    tenant_id = tenant_uuid(user)
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    return await _serialize_workspace(conn, workspace_id=workspace_id, tenant_id=tenant_id)


@router.post("/{process_id}", response_model=BidWorkspaceResponse, status_code=201)
async def ensure_bid_workspace(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> BidWorkspaceResponse:
    process_exists = (
        await conn.execute(
            sa.select(procurement_processes.c.id).where(procurement_processes.c.id == process_id)
        )
    ).scalar_one_or_none()
    if process_exists is None:
        raise HTTPException(status_code=404, detail="procurement process not found")
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    default_deadline = (
        await conn.execute(
            sa.select(sa.func.min(procurement_acts.c.submission_deadline)).where(
                procurement_acts.c.process_id == process_id,
                procurement_acts.c.submission_deadline.is_not(None),
            )
        )
    ).scalar_one_or_none()
    workspace_id = uuid.uuid4()
    actual_id = (
        await conn.execute(
            pg_insert(bid_workspaces)
            .values(
                id=workspace_id,
                tenant_id=tenant_id,
                process_id=process_id,
                owner_user_id=user_id,
                submission_due_at=default_deadline,
            )
            .on_conflict_do_nothing(
                index_elements=[bid_workspaces.c.tenant_id, bid_workspaces.c.process_id]
            )
            .returning(bid_workspaces.c.id)
        )
    ).scalar_one_or_none()
    actual_id = actual_id or await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.workspace.opened",
        object_id=actual_id,
        details={"process_id": str(process_id)},
    )
    response = await _serialize_workspace(
        conn, workspace_id=actual_id, tenant_id=tenant_id
    )
    await conn.commit()
    return response


@router.patch("/{process_id}", response_model=BidWorkspaceResponse)
async def update_bid_workspace(
    process_id: uuid.UUID,
    body: BidWorkspaceUpdate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> BidWorkspaceResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    values = body.model_dump(exclude_unset=True)
    if "owner_user_id" in values:
        await _validate_tenant_user(
            conn,
            tenant_id=tenant_id,
            user_id=values["owner_user_id"],
        )
    if "status" in values:
        values["status"] = str(values["status"]).upper()
        if values["status"] not in _WORKSPACE_STATUSES:
            raise HTTPException(status_code=422, detail="invalid bid workspace status")
    if "decision" in values:
        values["decision"] = str(values["decision"]).upper()
        if values["decision"] not in _DECISIONS:
            raise HTTPException(status_code=422, detail="invalid bid decision")
        values["decision_by"] = user_id
        values["decision_at"] = datetime.now(timezone.utc)
    values["updated_at"] = datetime.now(timezone.utc)
    await conn.execute(
        bid_workspaces.update()
        .where(
            bid_workspaces.c.id == workspace_id,
            bid_workspaces.c.tenant_id == tenant_id,
        )
        .values(**values)
    )
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.workspace.updated",
        object_id=workspace_id,
        details={key: str(value) if isinstance(value, uuid.UUID) else value for key, value in values.items()},
    )
    response = await _serialize_workspace(
        conn, workspace_id=workspace_id, tenant_id=tenant_id
    )
    await conn.commit()
    return response


@router.post("/{process_id}/tasks", response_model=BidTaskResponse, status_code=201)
async def create_bid_task(
    process_id: uuid.UUID,
    body: BidTaskCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> BidTaskResponse:
    status = body.status.upper()
    priority = body.priority.upper()
    if status not in _TASK_STATUSES or priority not in _PRIORITIES:
        raise HTTPException(status_code=422, detail="invalid task status or priority")
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    await _validate_tenant_user(
        conn,
        tenant_id=tenant_id,
        user_id=body.assigned_user_id,
    )
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    row = (
        await conn.execute(
            bid_tasks.insert()
            .values(
                id=uuid.uuid4(),
                bid_workspace_id=workspace_id,
                tenant_id=tenant_id,
                created_by=user_id,
                title=body.title,
                description=body.description,
                assigned_user_id=body.assigned_user_id,
                status=status,
                priority=priority,
                due_at=body.due_at,
            )
            .returning(bid_tasks)
        )
    ).one()
    await conn.execute(
        bid_workspaces.update()
        .where(bid_workspaces.c.id == workspace_id)
        .values(updated_at=sa.func.now())
    )
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.task.created",
        object_id=workspace_id,
        details={"task_id": str(row.id), "title": row.title},
    )
    await conn.commit()
    return _task_response(row)


@router.patch("/{process_id}/tasks/{task_id}", response_model=BidTaskResponse)
async def update_bid_task(
    process_id: uuid.UUID,
    task_id: uuid.UUID,
    body: BidTaskUpdate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> BidTaskResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    values = body.model_dump(exclude_unset=True)
    if "assigned_user_id" in values:
        await _validate_tenant_user(
            conn,
            tenant_id=tenant_id,
            user_id=values["assigned_user_id"],
        )
    if "status" in values:
        values["status"] = str(values["status"]).upper()
        if values["status"] not in _TASK_STATUSES:
            raise HTTPException(status_code=422, detail="invalid task status")
    if "priority" in values:
        values["priority"] = str(values["priority"]).upper()
        if values["priority"] not in _PRIORITIES:
            raise HTTPException(status_code=422, detail="invalid task priority")
    values["updated_at"] = datetime.now(timezone.utc)
    row = (
        await conn.execute(
            bid_tasks.update()
            .where(
                bid_tasks.c.id == task_id,
                bid_tasks.c.bid_workspace_id == workspace_id,
                bid_tasks.c.tenant_id == tenant_id,
            )
            .values(**values)
            .returning(bid_tasks)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="bid task not found")
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.task.updated",
        object_id=workspace_id,
        details={"task_id": str(task_id), "fields": sorted(values)},
    )
    await conn.commit()
    return _task_response(row)


@router.delete("/{process_id}/tasks/{task_id}", status_code=204)
async def delete_bid_task(
    process_id: uuid.UUID,
    task_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> None:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    result = await conn.execute(
        bid_tasks.delete().where(
            bid_tasks.c.id == task_id,
            bid_tasks.c.bid_workspace_id == workspace_id,
            bid_tasks.c.tenant_id == tenant_id,
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="bid task not found")
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.task.deleted",
        object_id=workspace_id,
        details={"task_id": str(task_id)},
    )
    await conn.commit()


@router.post("/{process_id}/requirements", response_model=BidRequirementResponse, status_code=201)
async def create_bid_requirement(
    process_id: uuid.UUID,
    body: BidRequirementCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> BidRequirementResponse:
    requirement_type = body.requirement_type.upper()
    status = body.status.upper()
    if requirement_type not in _REQUIREMENT_TYPES or status not in _REQUIREMENT_STATUSES:
        raise HTTPException(status_code=422, detail="invalid requirement type or status")
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    await _validate_tenant_user(
        conn,
        tenant_id=tenant_id,
        user_id=body.owner_user_id,
    )
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    if body.evidence_document_id is not None:
        document_exists = (
            await conn.execute(
                sa.select(documents.c.id)
                .join(
                    document_act_links,
                    document_act_links.c.document_id == documents.c.id,
                )
                .join(
                    procurement_acts,
                    procurement_acts.c.id == document_act_links.c.act_id,
                )
                .where(
                    documents.c.id == body.evidence_document_id,
                    procurement_acts.c.process_id == process_id,
                )
            )
        ).scalar_one_or_none()
        if document_exists is None:
            raise HTTPException(status_code=422, detail="evidence document does not belong to this process")
    row = (
        await conn.execute(
            bid_requirements.insert()
            .values(
                id=uuid.uuid4(),
                bid_workspace_id=workspace_id,
                tenant_id=tenant_id,
                created_by=user_id,
                requirement_type=requirement_type,
                title=body.title,
                description=body.description,
                status=status,
                mandatory=body.mandatory,
                evidence_document_id=body.evidence_document_id,
                evidence_page=body.evidence_page,
                source_excerpt=body.source_excerpt,
                owner_user_id=body.owner_user_id,
                due_at=body.due_at,
            )
            .returning(bid_requirements)
        )
    ).one()
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.requirement.created",
        object_id=workspace_id,
        details={"requirement_id": str(row.id), "type": requirement_type},
    )
    await conn.commit()
    return _requirement_response(row)


@router.patch("/{process_id}/requirements/{requirement_id}", response_model=BidRequirementResponse)
async def update_bid_requirement(
    process_id: uuid.UUID,
    requirement_id: uuid.UUID,
    body: BidRequirementUpdate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> BidRequirementResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    values = body.model_dump(exclude_unset=True)
    if "owner_user_id" in values:
        await _validate_tenant_user(
            conn,
            tenant_id=tenant_id,
            user_id=values["owner_user_id"],
        )
    if "requirement_type" in values:
        values["requirement_type"] = str(values["requirement_type"]).upper()
        if values["requirement_type"] not in _REQUIREMENT_TYPES:
            raise HTTPException(status_code=422, detail="invalid requirement type")
    if "status" in values:
        values["status"] = str(values["status"]).upper()
        if values["status"] not in _REQUIREMENT_STATUSES:
            raise HTTPException(status_code=422, detail="invalid requirement status")
    values["updated_at"] = datetime.now(timezone.utc)
    row = (
        await conn.execute(
            bid_requirements.update()
            .where(
                bid_requirements.c.id == requirement_id,
                bid_requirements.c.bid_workspace_id == workspace_id,
                bid_requirements.c.tenant_id == tenant_id,
            )
            .values(**values)
            .returning(bid_requirements)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="bid requirement not found")
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.requirement.updated",
        object_id=workspace_id,
        details={"requirement_id": str(requirement_id), "fields": sorted(values)},
    )
    await conn.commit()
    return _requirement_response(row)


@router.delete("/{process_id}/requirements/{requirement_id}", status_code=204)
async def delete_bid_requirement(
    process_id: uuid.UUID,
    requirement_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> None:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn,
        process_id=process_id,
        tenant_id=tenant_id,
    )
    result = await conn.execute(
        bid_requirements.delete().where(
            bid_requirements.c.id == requirement_id,
            bid_requirements.c.bid_workspace_id == workspace_id,
            bid_requirements.c.tenant_id == tenant_id,
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="bid requirement not found")
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.requirement.deleted",
        object_id=workspace_id,
        details={"requirement_id": str(requirement_id)},
    )
    await conn.commit()


async def _validate_workspace_child(
    conn: AsyncConnection,
    *,
    workspace_id: uuid.UUID,
    tenant_id: uuid.UUID,
    task_id: uuid.UUID | None,
    requirement_id: uuid.UUID | None,
) -> None:
    if task_id is not None:
        exists = (
            await conn.execute(
                sa.select(bid_tasks.c.id).where(
                    bid_tasks.c.id == task_id,
                    bid_tasks.c.bid_workspace_id == workspace_id,
                    bid_tasks.c.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=422, detail="task does not belong to this bid workspace")
    if requirement_id is not None:
        exists = (
            await conn.execute(
                sa.select(bid_requirements.c.id).where(
                    bid_requirements.c.id == requirement_id,
                    bid_requirements.c.bid_workspace_id == workspace_id,
                    bid_requirements.c.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=422, detail="requirement does not belong to this bid workspace")


@router.post("/{process_id}/comments", response_model=dict, status_code=201)
async def create_bid_comment(
    process_id: uuid.UUID,
    body: BidCommentCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    await _validate_workspace_child(
        conn,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        task_id=body.task_id,
        requirement_id=body.requirement_id,
    )
    row = (
        await conn.execute(
            bid_comments.insert()
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                bid_workspace_id=workspace_id,
                author_user_id=user_id,
                **body.model_dump(),
            )
            .returning(bid_comments)
        )
    ).mappings().one()
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.comment.created",
        object_id=workspace_id,
        details={"comment_id": str(row["id"])},
    )
    await conn.commit()
    return dict(row)


@router.patch("/{process_id}/comments/{comment_id}", response_model=dict)
async def update_bid_comment(
    process_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: BidCommentUpdate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    condition = [
        bid_comments.c.id == comment_id,
        bid_comments.c.bid_workspace_id == workspace_id,
        bid_comments.c.tenant_id == tenant_id,
    ]
    if user.role not in {"OWNER", "ADMIN"}:
        condition.append(bid_comments.c.author_user_id == user_id)
    row = (
        await conn.execute(
            bid_comments.update()
            .where(*condition)
            .values(body=body.body, updated_at=sa.func.now())
            .returning(bid_comments)
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="comment not found")
    await conn.commit()
    return dict(row)


@router.delete("/{process_id}/comments/{comment_id}", status_code=204)
async def delete_bid_comment(
    process_id: uuid.UUID,
    comment_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> None:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    condition = [
        bid_comments.c.id == comment_id,
        bid_comments.c.bid_workspace_id == workspace_id,
        bid_comments.c.tenant_id == tenant_id,
    ]
    if user.role not in {"OWNER", "ADMIN"}:
        condition.append(bid_comments.c.author_user_id == user_id)
    result = await conn.execute(bid_comments.delete().where(*condition))
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="comment not found")
    await conn.commit()


@router.post("/{process_id}/reminders", response_model=dict, status_code=201)
async def create_bid_reminder(
    process_id: uuid.UUID,
    body: BidReminderCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    channel = body.channel.upper()
    if channel not in {"IN_APP", "EMAIL", "WEBHOOK"}:
        raise HTTPException(status_code=422, detail="invalid reminder channel")
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    await _validate_tenant_user(conn, tenant_id=tenant_id, user_id=body.assigned_user_id)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    await _validate_workspace_child(
        conn,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        task_id=body.task_id,
        requirement_id=body.requirement_id,
    )
    row = (
        await conn.execute(
            bid_reminders.insert()
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                bid_workspace_id=workspace_id,
                task_id=body.task_id,
                requirement_id=body.requirement_id,
                assigned_user_id=body.assigned_user_id or user_id,
                remind_at=body.remind_at,
                channel=channel,
                created_by=user_id,
            )
            .returning(bid_reminders)
        )
    ).mappings().one()
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.reminder.created",
        object_id=workspace_id,
        details={"reminder_id": str(row["id"]), "remind_at": row["remind_at"].isoformat()},
    )
    await conn.commit()
    return dict(row)


@router.patch("/{process_id}/reminders/{reminder_id}", response_model=dict)
async def update_bid_reminder(
    process_id: uuid.UUID,
    reminder_id: uuid.UUID,
    body: BidReminderUpdate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    values = body.model_dump(exclude_unset=True)
    if "assigned_user_id" in values:
        await _validate_tenant_user(
            conn, tenant_id=tenant_id, user_id=values["assigned_user_id"]
        )
    if "channel" in values and values["channel"] is not None:
        values["channel"] = str(values["channel"]).upper()
        if values["channel"] not in {"IN_APP", "EMAIL", "WEBHOOK"}:
            raise HTTPException(status_code=422, detail="invalid reminder channel")
    if "status" in values and values["status"] is not None:
        values["status"] = str(values["status"]).upper()
        if values["status"] not in {"PENDING", "SENT", "DISMISSED", "FAILED"}:
            raise HTTPException(status_code=422, detail="invalid reminder status")
        if values["status"] == "SENT":
            values["sent_at"] = datetime.now(timezone.utc)
    row = (
        await conn.execute(
            bid_reminders.update()
            .where(
                bid_reminders.c.id == reminder_id,
                bid_reminders.c.bid_workspace_id == workspace_id,
                bid_reminders.c.tenant_id == tenant_id,
            )
            .values(**values)
            .returning(bid_reminders)
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="reminder not found")
    await conn.commit()
    return dict(row)


@router.post("/{process_id}/reminders/{reminder_id}/retry", response_model=dict)
async def retry_bid_reminder(
    process_id: uuid.UUID,
    reminder_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    row = (
        await conn.execute(
            bid_reminders.update()
            .where(
                bid_reminders.c.id == reminder_id,
                bid_reminders.c.bid_workspace_id == workspace_id,
                bid_reminders.c.tenant_id == tenant_id,
                bid_reminders.c.status == "FAILED",
            )
            .values(
                status="PENDING",
                sent_at=None,
                attempt_count=0,
                last_attempt_at=None,
                next_retry_at=None,
                last_error=None,
            )
            .returning(bid_reminders)
        )
    ).mappings().first()
    if row is None:
        exists = await conn.scalar(
            sa.select(sa.literal(True)).where(
                bid_reminders.c.id == reminder_id,
                bid_reminders.c.bid_workspace_id == workspace_id,
                bid_reminders.c.tenant_id == tenant_id,
            )
        )
        if exists:
            raise HTTPException(status_code=409, detail="only failed reminders can be retried")
        raise HTTPException(status_code=404, detail="reminder not found")
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.reminder.retried",
        object_id=workspace_id,
        details={"reminder_id": str(reminder_id)},
    )
    await conn.commit()
    return dict(row)


@router.delete("/{process_id}/reminders/{reminder_id}", status_code=204)
async def delete_bid_reminder(
    process_id: uuid.UUID,
    reminder_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> None:
    tenant_id = tenant_uuid(user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    result = await conn.execute(
        bid_reminders.delete().where(
            bid_reminders.c.id == reminder_id,
            bid_reminders.c.bid_workspace_id == workspace_id,
            bid_reminders.c.tenant_id == tenant_id,
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="reminder not found")
    await conn.commit()


@router.post("/{process_id}/certificates", response_model=dict, status_code=201)
async def link_bid_certificate(
    process_id: uuid.UUID,
    body: CertificateLinkCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    certificate = (
        await conn.execute(
            sa.select(tenant_certificates.c.id).where(
                tenant_certificates.c.id == body.certificate_id,
                tenant_certificates.c.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if certificate is None:
        raise HTTPException(status_code=404, detail="certificate not found")
    await _validate_workspace_child(
        conn,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        task_id=None,
        requirement_id=body.requirement_id,
    )
    row = (
        await conn.execute(
            pg_insert(bid_certificate_links)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                bid_workspace_id=workspace_id,
                requirement_id=body.requirement_id,
                certificate_id=body.certificate_id,
                linked_by=user_id,
            )
            .on_conflict_do_nothing()
            .returning(bid_certificate_links)
        )
    ).mappings().first()
    if row is None:
        row = (
            await conn.execute(
                sa.select(bid_certificate_links).where(
                    bid_certificate_links.c.bid_workspace_id == workspace_id,
                    bid_certificate_links.c.requirement_id.is_(body.requirement_id)
                    if body.requirement_id is None
                    else bid_certificate_links.c.requirement_id == body.requirement_id,
                    bid_certificate_links.c.certificate_id == body.certificate_id,
                )
            )
        ).mappings().one()
    await conn.commit()
    return dict(row)


@router.delete("/{process_id}/certificates/{link_id}", status_code=204)
async def unlink_bid_certificate(
    process_id: uuid.UUID,
    link_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> None:
    tenant_id = tenant_uuid(user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    result = await conn.execute(
        bid_certificate_links.delete().where(
            bid_certificate_links.c.id == link_id,
            bid_certificate_links.c.bid_workspace_id == workspace_id,
            bid_certificate_links.c.tenant_id == tenant_id,
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="certificate link not found")
    await conn.commit()


@router.post("/{process_id}/crm-handoffs", response_model=dict, status_code=201)
async def create_crm_handoff(
    process_id: uuid.UUID,
    body: CrmHandoffCreate,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    http_client: httpx.AsyncClient = Depends(get_http_client),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace_id = await _workspace_id_for_process(
        conn, process_id=process_id, tenant_id=tenant_id
    )
    provider = body.provider.upper()
    env_provider = "".join(character if character.isalnum() else "_" for character in provider)
    webhook_url = os.environ.get(f"CRM_{env_provider}_WEBHOOK_URL")
    payload = {
        "process_id": str(process_id),
        "bid_workspace_id": str(workspace_id),
        **body.payload,
    }
    handoff_id = uuid.uuid4()
    await conn.execute(
        crm_handoffs.insert().values(
            id=handoff_id,
            tenant_id=tenant_id,
            bid_workspace_id=workspace_id,
            provider=provider,
            payload=payload,
            created_by=user_id,
        )
    )
    status = "FAILED"
    response_body: dict | list | str | None = None
    error_message: str | None = None
    external_reference: str | None = None
    if not webhook_url:
        error_message = f"CRM_{env_provider}_WEBHOOK_URL is not configured"
    else:
        try:
            response = await http_client.post(webhook_url, json=payload, timeout=20)
            response.raise_for_status()
            try:
                response_body = response.json()
            except ValueError:
                response_body = response.text[:4000]
            if isinstance(response_body, dict):
                external_reference = str(
                    response_body.get("id")
                    or response_body.get("external_reference")
                    or ""
                ) or None
            status = "SYNCED"
        except httpx.HTTPError as exc:
            error_message = str(exc)
    row = (
        await conn.execute(
            crm_handoffs.update()
            .where(crm_handoffs.c.id == handoff_id)
            .values(
                status=status,
                response=response_body,
                error_message=error_message,
                external_reference=external_reference,
                synced_at=datetime.now(timezone.utc) if status == "SYNCED" else None,
            )
            .returning(crm_handoffs)
        )
    ).mappings().one()
    await _audit(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="bid.crm_handoff",
        object_id=workspace_id,
        details={"handoff_id": str(handoff_id), "provider": provider, "status": status},
    )
    await conn.commit()
    return dict(row)

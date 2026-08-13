"""Tenant-scoped asynchronous CSV/XLSX exports."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import audit_log, export_jobs
from packages.object_storage import configured_object_store
from services.product.entitlements import EntitlementLimitExceeded, consume_entitlement

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/exports", tags=["exports"])
_EXPORT_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER", "API_CLIENT")


class ExportCreateRequest(BaseModel):
    export_type: Literal["OPPORTUNITIES", "PIPELINE", "BUYERS", "SUPPLIERS", "RELATIONSHIPS"]
    format: Literal["CSV", "XLSX"] = "CSV"
    filters: dict[str, Any] = Field(default_factory=dict)


class ExportJobResponse(BaseModel):
    id: str
    export_type: str
    format: str
    filters: dict[str, Any]
    status: str
    row_count: int | None
    file_name: str | None
    error: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None
    download_url: str | None


def _job(row: Any) -> ExportJobResponse:
    return ExportJobResponse(
        id=str(row.id), export_type=row.export_type, format=row.format, filters=row.filters,
        status=row.status, row_count=row.row_count, file_name=row.file_name, error=row.error,
        created_at=row.created_at, finished_at=row.finished_at, expires_at=row.expires_at,
        download_url=f"/v1/exports/{row.id}/download" if row.status == "SUCCEEDED" else None,
    )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid export id") from exc


@router.get("", response_model=list[ExportJobResponse])
async def list_exports(
    limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[ExportJobResponse]:
    rows = (await conn.execute(sa.select(export_jobs).where(
        export_jobs.c.tenant_id == tenant_uuid(user),
    ).order_by(export_jobs.c.created_at.desc()).limit(limit))).all()
    return [_job(row) for row in rows]


@router.post("", response_model=ExportJobResponse, status_code=202)
async def create_export(
    body: ExportCreateRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_EXPORT_ROLES)),
) -> ExportJobResponse:
    try:
        await consume_entitlement(
            conn,
            tenant_id=tenant_uuid(user),
            metric_code="exports_month",
        )
    except EntitlementLimitExceeded as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "ENTITLEMENT_LIMIT",
                "metric": exc.metric_code,
                "limit": exc.limit,
                "usage": exc.usage,
            },
        ) from exc
    user_id = await ensure_workspace_user(conn, user)
    job_id = uuid.uuid4()
    await conn.execute(export_jobs.insert().values(
        id=job_id, tenant_id=tenant_uuid(user), user_id=user_id,
        export_type=body.export_type, format=body.format, filters=body.filters,
    ))
    await conn.execute(audit_log.insert().values(
        id=uuid.uuid4(), tenant_id=tenant_uuid(user), actor_user_id=user_id,
        action="export.created", object_type="export_job", object_id=job_id,
        details={"type": body.export_type, "format": body.format},
    ))
    row = (await conn.execute(sa.select(export_jobs).where(export_jobs.c.id == job_id))).one()
    await conn.commit()
    return _job(row)


@router.post("/{job_id}/retry", response_model=ExportJobResponse, status_code=202)
async def retry_export(
    job_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_EXPORT_ROLES)),
) -> ExportJobResponse:
    target = _uuid(job_id)
    row = (await conn.execute(export_jobs.update().where(
        export_jobs.c.id == target, export_jobs.c.tenant_id == tenant_uuid(user),
        export_jobs.c.status == "FAILED",
    ).values(status="PENDING", error=None).returning(export_jobs))).first()
    if row is None:
        raise HTTPException(status_code=409, detail="Only failed exports can be retried")
    await conn.commit()
    return _job(row)


@router.get("/{job_id}/download", response_model=None)
async def download_export(
    job_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> FileResponse | RedirectResponse:
    row = (await conn.execute(sa.select(export_jobs).where(
        export_jobs.c.id == _uuid(job_id), export_jobs.c.tenant_id == tenant_uuid(user),
    ))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    if row.status != "SUCCEEDED" or not row.storage_path:
        raise HTTPException(status_code=409, detail="Export is not ready")
    if row.expires_at and row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Export has expired")
    object_store = configured_object_store(
        local_root=os.environ.get("EXPORT_ROOT", "raw/exports"),
        s3_prefix=os.environ.get("OBJECT_STORAGE_EXPORT_PREFIX", "exports"),
    )
    try:
        signed_url = await object_store.presign_get(
            row.storage_path,
            expires_seconds=300,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Export file is unavailable") from exc
    if signed_url:
        return RedirectResponse(signed_url, status_code=307)
    return FileResponse(row.storage_path, media_type=row.mime_type, filename=row.file_name)

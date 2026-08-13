"""Bulk document operations, editable conversion, phrase monitors and sector profiles."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.responses import StreamingResponse

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    document_act_links,
    document_pages,
    document_phrase_matches,
    document_phrase_monitors,
    document_transformation_jobs,
    documents,
    procurement_acts,
    procurement_processes,
    sector_profile_templates,
)
from packages.object_storage import configured_object_store
from packages.network_safety import validate_public_http_url
from services.product.document_tools import (
    evaluate_phrase_monitor,
    render_editable_document_docx,
    safe_archive_name,
)

from ..auth import get_current_user, require_role
from ..db import get_conn, get_tenant_scoped_conn
from ..deps import get_http_client
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(tags=["document-tools"])
_WRITE_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")
_MAX_DOCUMENT_BYTES = int(os.environ.get("DOCUMENT_TOOL_MAX_BYTES", str(50 * 1024 * 1024)))
_MAX_ARCHIVE_BYTES = int(os.environ.get("DOCUMENT_TOOL_MAX_ARCHIVE_BYTES", str(500 * 1024 * 1024)))


class SectorProfileResponse(BaseModel):
    code: str
    name: str
    description: str
    cpv_prefixes: list[str]
    keywords: list[str]
    excluded_keywords: list[str]
    recommended_alerts: list[dict[str, Any]]


class PhraseMonitorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phrases: list[str] = Field(min_length=1, max_length=30)
    match_mode: str = "ANY"
    cpv_prefixes: list[str] = Field(default_factory=list, max_length=50)
    is_active: bool = True


class PhraseMatchResponse(BaseModel):
    id: str
    document_id: str
    process_id: str | None
    document_title: str | None
    process_title: str | None
    matched_phrases: list[str]
    page_numbers: list[int]
    excerpts: list[dict[str, Any]]
    matched_at: datetime


class PhraseMonitorResponse(BaseModel):
    id: str
    name: str
    phrases: list[str]
    match_mode: str
    cpv_prefixes: list[str]
    is_active: bool
    match_count: int
    matches: list[PhraseMatchResponse]
    created_at: datetime
    updated_at: datetime


async def _document_payload(
    row: Any,
    *,
    http_client: httpx.AsyncClient,
) -> bytes:
    object_uri = str(row.object_uri or "")
    if object_uri:
        object_store = configured_object_store(
            local_root=os.environ.get("DOCUMENT_STORE_ROOT", "./data/documents"),
            s3_prefix=os.environ.get("OBJECT_STORAGE_DOCUMENT_PREFIX", "documents"),
        )
        try:
            return await object_store.get(object_uri, max_bytes=_MAX_DOCUMENT_BYTES)
        except (FileNotFoundError, OSError, ValueError):
            pass
    if not row.source_url:
        raise ValueError("no safe retrievable official URL")
    await validate_public_http_url(
        row.source_url,
        allow_test_hosts=os.environ.get("PROCINTEL_ENV", "development").casefold() != "production",
    )
    chunks: list[bytes] = []
    total = 0
    async with http_client.stream("GET", row.source_url, follow_redirects=False) as response:
        response.raise_for_status()
        declared_size = int(response.headers.get("content-length") or 0)
        if declared_size > _MAX_DOCUMENT_BYTES:
            raise ValueError("downloaded document exceeds size limit")
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_DOCUMENT_BYTES:
                raise ValueError("downloaded document exceeds size limit")
            chunks.append(chunk)
    return b"".join(chunks)


async def _stream_file_and_remove(path: Path):
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                yield chunk
    finally:
        path.unlink(missing_ok=True)


async def _monitor_response(
    conn: AsyncConnection,
    row: Any,
) -> PhraseMonitorResponse:
    matches = (
        await conn.execute(
            sa.select(
                document_phrase_matches,
                documents.c.title.label("document_title"),
                procurement_processes.c.title.label("process_title"),
            )
            .join(documents, documents.c.id == document_phrase_matches.c.document_id)
            .outerjoin(
                procurement_processes,
                procurement_processes.c.id == document_phrase_matches.c.process_id,
            )
            .where(document_phrase_matches.c.monitor_id == row.id)
            .order_by(document_phrase_matches.c.matched_at.desc())
            .limit(100)
        )
    ).all()
    return PhraseMonitorResponse(
        id=str(row.id),
        name=row.name,
        phrases=list(row.phrases or []),
        match_mode=row.match_mode,
        cpv_prefixes=list(row.cpv_prefixes or []),
        is_active=row.is_active,
        match_count=len(matches),
        matches=[
            PhraseMatchResponse(
                id=str(item.id),
                document_id=str(item.document_id),
                process_id=str(item.process_id) if item.process_id else None,
                document_title=item.document_title,
                process_title=item.process_title,
                matched_phrases=list(item.matched_phrases or []),
                page_numbers=list(item.page_numbers or []),
                excerpts=list(item.excerpts or []),
                matched_at=item.matched_at,
            )
            for item in matches
        ],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/v1/sector-profiles", response_model=list[SectorProfileResponse])
async def list_sector_profiles(
    conn: AsyncConnection = Depends(get_conn),
) -> list[SectorProfileResponse]:
    rows = (
        await conn.execute(
            sa.select(sector_profile_templates)
            .where(sector_profile_templates.c.is_active.is_(True))
            .order_by(sector_profile_templates.c.display_order)
        )
    ).all()
    return [
        SectorProfileResponse(
            code=row.code,
            name=row.name,
            description=row.description,
            cpv_prefixes=list(row.cpv_prefixes or []),
            keywords=list(row.keywords or []),
            excluded_keywords=list(row.excluded_keywords or []),
            recommended_alerts=list(row.recommended_alerts or []),
        )
        for row in rows
    ]


@router.get("/v1/document-tools/phrase-monitors", response_model=list[PhraseMonitorResponse])
async def list_phrase_monitors(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> list[PhraseMonitorResponse]:
    rows = (
        await conn.execute(
            sa.select(document_phrase_monitors)
            .where(document_phrase_monitors.c.tenant_id == tenant_uuid(user))
            .order_by(document_phrase_monitors.c.created_at.desc())
        )
    ).all()
    return [await _monitor_response(conn, row) for row in rows]


@router.post(
    "/v1/document-tools/phrase-monitors",
    response_model=PhraseMonitorResponse,
    status_code=201,
)
async def create_phrase_monitor(
    body: PhraseMonitorRequest,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> PhraseMonitorResponse:
    mode = body.match_mode.upper()
    if mode not in {"ANY", "ALL", "EXACT"}:
        raise HTTPException(status_code=422, detail="match_mode must be ANY, ALL or EXACT")
    user_id = await ensure_workspace_user(conn, user)
    monitor_id = uuid.uuid4()
    row = (
        await conn.execute(
            document_phrase_monitors.insert()
            .values(
                id=monitor_id,
                tenant_id=tenant_uuid(user),
                user_id=user_id,
                name=body.name,
                phrases=list(dict.fromkeys(phrase.strip() for phrase in body.phrases if phrase.strip())),
                match_mode=mode,
                cpv_prefixes=list(dict.fromkeys(prefix.strip() for prefix in body.cpv_prefixes if prefix.strip())),
                is_active=body.is_active,
            )
            .returning(document_phrase_monitors)
        )
    ).one()
    await evaluate_phrase_monitor(conn, monitor_id=monitor_id)
    return await _monitor_response(conn, row)


@router.put(
    "/v1/document-tools/phrase-monitors/{monitor_id}",
    response_model=PhraseMonitorResponse,
)
async def update_phrase_monitor(
    monitor_id: uuid.UUID,
    body: PhraseMonitorRequest,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> PhraseMonitorResponse:
    mode = body.match_mode.upper()
    if mode not in {"ANY", "ALL", "EXACT"}:
        raise HTTPException(status_code=422, detail="match_mode must be ANY, ALL or EXACT")
    row = (
        await conn.execute(
            document_phrase_monitors.update()
            .where(
                document_phrase_monitors.c.id == monitor_id,
                document_phrase_monitors.c.tenant_id == tenant_uuid(user),
            )
            .values(
                name=body.name,
                phrases=list(dict.fromkeys(phrase.strip() for phrase in body.phrases if phrase.strip())),
                match_mode=mode,
                cpv_prefixes=list(dict.fromkeys(prefix.strip() for prefix in body.cpv_prefixes if prefix.strip())),
                is_active=body.is_active,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(document_phrase_monitors)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Phrase monitor not found")
    await evaluate_phrase_monitor(conn, monitor_id=monitor_id)
    return await _monitor_response(conn, row)


@router.delete("/v1/document-tools/phrase-monitors/{monitor_id}", status_code=204)
async def delete_phrase_monitor(
    monitor_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> Response:
    result = await conn.execute(
        document_phrase_monitors.delete().where(
            document_phrase_monitors.c.id == monitor_id,
            document_phrase_monitors.c.tenant_id == tenant_uuid(user),
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Phrase monitor not found")
    return Response(status_code=204)


@router.post(
    "/v1/document-tools/phrase-monitors/{monitor_id}/evaluate",
    response_model=PhraseMonitorResponse,
)
async def evaluate_monitor_now(
    monitor_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> PhraseMonitorResponse:
    row = (
        await conn.execute(
            sa.select(document_phrase_monitors).where(
                document_phrase_monitors.c.id == monitor_id,
                document_phrase_monitors.c.tenant_id == tenant_uuid(user),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Phrase monitor not found")
    await evaluate_phrase_monitor(conn, monitor_id=monitor_id)
    return await _monitor_response(conn, row)


@router.get("/v1/document-tools/process/{process_id}/bulk.zip")
async def bulk_download_process_documents(
    process_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> StreamingResponse:
    user_id = await ensure_workspace_user(conn, user)
    rows = (
        await conn.execute(
            sa.select(documents)
            .join(
                document_act_links,
                document_act_links.c.document_id == documents.c.id,
            )
            .join(
                procurement_acts,
                procurement_acts.c.id == document_act_links.c.act_id,
            )
            .where(procurement_acts.c.process_id == process_id)
            .order_by(documents.c.created_at)
            .distinct()
            .limit(50)
        )
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No downloaded documents are linked to this process")
    manifest_items: list[dict[str, Any]] = []
    archive_handle = tempfile.NamedTemporaryFile(prefix="procintel-documents-", suffix=".zip", delete=False)
    archive_path = Path(archive_handle.name)
    archive_handle.close()
    included = 0
    total_uncompressed = 0
    seen_names: dict[str, int] = {}
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for index, row in enumerate(rows, start=1):
                extension = mimetypes.guess_extension(row.mime_type or "") or Path(urlparse(row.source_url or "").path).suffix or ".bin"
                name = safe_archive_name(row.title or f"document-{index}", fallback=f"document-{index}") + extension
                duplicate_count = seen_names.get(name, 0)
                seen_names[name] = duplicate_count + 1
                if duplicate_count:
                    stem, dot, suffix = name.rpartition(".")
                    name = f"{stem or suffix}-{duplicate_count + 1}{dot}{suffix if dot else ''}"
                item = {
                    "document_id": str(row.id),
                    "title": row.title,
                    "official_url": row.source_url,
                    "sha256": row.sha256,
                    "status": "FAILED",
                }
                try:
                    payload = await _document_payload(row, http_client=http_client)
                    if total_uncompressed + len(payload) > _MAX_ARCHIVE_BYTES:
                        raise ValueError("archive exceeds aggregate size limit")
                    archive.writestr(name, payload)
                    total_uncompressed += len(payload)
                    included += 1
                    item["status"] = "INCLUDED"
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    item["error"] = f"{type(exc).__name__}: {exc}"
                manifest_items.append(item)
            manifest = {
            "process_id": str(process_id),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "documents": manifest_items,
            "included": included,
            "failed": len(rows) - included,
            "uncompressed_bytes": total_uncompressed,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    file_name = f"procintel-documents-{process_id}.zip"
    await conn.execute(
        document_transformation_jobs.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid(user),
            user_id=user_id,
            transformation_type="BULK_ZIP",
            document_ids=[row.id for row in rows],
            status="COMPLETED",
            file_name=file_name,
            finished_at=datetime.now(timezone.utc),
        )
    )
    return StreamingResponse(
        _stream_file_and_remove(archive_path),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/v1/document-tools/documents/{document_id}/convert.docx")
async def convert_document_to_word(
    document_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> StreamingResponse:
    user_id = await ensure_workspace_user(conn, user)
    row = (
        await conn.execute(sa.select(documents).where(documents.c.id == document_id))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    pages = (
        await conn.execute(
            sa.select(document_pages.c.page_number, document_pages.c.text)
            .where(document_pages.c.document_id == document_id)
            .order_by(document_pages.c.page_number)
        )
    ).all()
    if not pages:
        raise HTTPException(
            status_code=409,
            detail="Editable conversion requires completed text extraction or OCR",
        )
    payload = render_editable_document_docx(
        title=row.title or "Procintel document conversion",
        pages=[(item.page_number, item.text) for item in pages],
        source_url=row.source_url,
    )
    base = safe_archive_name(row.title or str(document_id), fallback=str(document_id))
    file_name = f"{base}.docx"
    await conn.execute(
        document_transformation_jobs.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid(user),
            user_id=user_id,
            transformation_type="PDF_TO_DOCX",
            document_ids=[document_id],
            status="COMPLETED",
            file_name=file_name,
            finished_at=datetime.now(timezone.utc),
        )
    )
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )

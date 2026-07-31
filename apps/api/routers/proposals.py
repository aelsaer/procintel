"""Evidence-grounded proposal drafting, version review and DOCX export."""

from __future__ import annotations

import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.responses import StreamingResponse

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    bid_requirements,
    bid_workspaces,
    documents,
    procurement_processes,
    proposal_exports,
    proposal_sections,
    proposal_section_versions,
    tenant_bid_content,
)
from services.intelligence.llm import generate_text
from services.product.proposals import (
    ReusableContent,
    deterministic_first_draft,
    proposal_prompt,
    render_proposal_docx,
    select_reusable_content,
)
from services.product.entitlements import EntitlementLimitExceeded, consume_entitlement

from ..auth import get_current_user, require_role
from ..db import get_tenant_scoped_conn
from ..deps import get_http_client
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/proposals", tags=["proposals"])
_WRITE_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")
_SECTION_STATUSES = {"DRAFT", "IN_REVIEW", "APPROVED", "NEEDS_CHANGES"}


class BidContentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content_type: str = Field(default="RESPONSE", min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    approved: bool = False


class BidContentResponse(BaseModel):
    id: str
    title: str
    content_type: str
    body: str
    tags: list[str]
    approved: bool
    created_at: datetime
    updated_at: datetime


class ProposalSectionResponse(BaseModel):
    id: str
    requirement_id: str | None
    section_key: str
    title: str
    display_order: int
    status: str
    current_version: int
    body: str
    citations: list[dict[str, Any]]
    generation_metadata: dict[str, Any]
    assigned_user_id: str | None
    updated_at: datetime


class ProposalWorkspaceResponse(BaseModel):
    process_id: str
    bid_workspace_id: str
    process_title: str | None
    sections: list[ProposalSectionResponse]
    requirements_total: int
    requirements_mapped: int
    approved_sections: int


class ProposalGenerateRequest(BaseModel):
    requirement_ids: list[uuid.UUID] = Field(default_factory=list)
    language: str = Field(default="el-GR", max_length=20)
    additional_instructions: str | None = Field(default=None, max_length=3000)


class ProposalSectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    body: str | None = Field(default=None, min_length=1, max_length=200_000)
    status: str | None = None
    assigned_user_id: uuid.UUID | None = None
    change_summary: str | None = Field(default=None, max_length=1000)


class ProposalVersionResponse(BaseModel):
    id: str
    version_number: int
    body: str
    citations: list[dict[str, Any]]
    change_summary: str | None
    created_by: str
    created_at: datetime


def _content_response(row: Any) -> BidContentResponse:
    return BidContentResponse(
        id=str(row.id),
        title=row.title,
        content_type=row.content_type,
        body=row.body,
        tags=list(row.tags or []),
        approved=row.approved,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _section_response(row: Any) -> ProposalSectionResponse:
    return ProposalSectionResponse(
        id=str(row.id),
        requirement_id=str(row.requirement_id) if row.requirement_id else None,
        section_key=row.section_key,
        title=row.title,
        display_order=row.display_order,
        status=row.status,
        current_version=row.current_version,
        body=row.body,
        citations=list(row.citations or []),
        generation_metadata=dict(row.generation_metadata or {}),
        assigned_user_id=str(row.assigned_user_id) if row.assigned_user_id else None,
        updated_at=row.updated_at,
    )


async def _workspace(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Any:
    row = (
        await conn.execute(
            sa.select(
                bid_workspaces,
                procurement_processes.c.title.label("process_title"),
                procurement_processes.c.public_id,
            )
            .join(procurement_processes, procurement_processes.c.id == bid_workspaces.c.process_id)
            .where(
                bid_workspaces.c.process_id == process_id,
                bid_workspaces.c.tenant_id == tenant_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Create the bid workspace before drafting a proposal")
    return row


async def _proposal_response(
    conn: AsyncConnection,
    *,
    workspace: Any,
    tenant_id: uuid.UUID,
) -> ProposalWorkspaceResponse:
    rows = (
        await conn.execute(
            sa.select(proposal_sections)
            .where(
                proposal_sections.c.bid_workspace_id == workspace.id,
                proposal_sections.c.tenant_id == tenant_id,
            )
            .order_by(proposal_sections.c.display_order, proposal_sections.c.created_at)
        )
    ).all()
    requirement_count = (
        await conn.execute(
            sa.select(sa.func.count())
            .select_from(bid_requirements)
            .where(
                bid_requirements.c.bid_workspace_id == workspace.id,
                bid_requirements.c.tenant_id == tenant_id,
            )
        )
    ).scalar_one()
    return ProposalWorkspaceResponse(
        process_id=str(workspace.process_id),
        bid_workspace_id=str(workspace.id),
        process_title=workspace.process_title,
        sections=[_section_response(row) for row in rows],
        requirements_total=int(requirement_count),
        requirements_mapped=sum(row.requirement_id is not None for row in rows),
        approved_sections=sum(row.status == "APPROVED" for row in rows),
    )


async def _write_version(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    section_id: uuid.UUID,
    version_number: int,
    body: str,
    citations: list[dict[str, Any]],
    change_summary: str | None,
    user_id: uuid.UUID,
) -> None:
    await conn.execute(
        proposal_section_versions.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            proposal_section_id=section_id,
            version_number=version_number,
            body=body,
            citations=citations,
            change_summary=change_summary,
            created_by=user_id,
        )
    )


@router.get("/library", response_model=list[BidContentResponse])
async def list_bid_content(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> list[BidContentResponse]:
    rows = (
        await conn.execute(
            sa.select(tenant_bid_content)
            .where(tenant_bid_content.c.tenant_id == tenant_uuid(user))
            .order_by(tenant_bid_content.c.approved.desc(), tenant_bid_content.c.updated_at.desc())
        )
    ).all()
    return [_content_response(row) for row in rows]


@router.post("/library", response_model=BidContentResponse, status_code=201)
async def create_bid_content(
    body: BidContentRequest,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> BidContentResponse:
    user_id = await ensure_workspace_user(conn, user)
    now = datetime.now(timezone.utc)
    item_id = uuid.uuid4()
    row = (
        await conn.execute(
            tenant_bid_content.insert()
            .values(
                id=item_id,
                tenant_id=tenant_uuid(user),
                title=body.title,
                content_type=body.content_type.upper(),
                body=body.body,
                tags=list(dict.fromkeys(tag.strip() for tag in body.tags if tag.strip())),
                approved=body.approved,
                approved_by=user_id if body.approved else None,
                approved_at=now if body.approved else None,
                created_by=user_id,
                updated_at=now,
            )
            .returning(tenant_bid_content)
        )
    ).one()
    return _content_response(row)


@router.patch("/library/{content_id}", response_model=BidContentResponse)
async def update_bid_content(
    content_id: uuid.UUID,
    body: BidContentRequest,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> BidContentResponse:
    user_id = await ensure_workspace_user(conn, user)
    now = datetime.now(timezone.utc)
    row = (
        await conn.execute(
            tenant_bid_content.update()
            .where(
                tenant_bid_content.c.id == content_id,
                tenant_bid_content.c.tenant_id == tenant_uuid(user),
            )
            .values(
                title=body.title,
                content_type=body.content_type.upper(),
                body=body.body,
                tags=list(dict.fromkeys(tag.strip() for tag in body.tags if tag.strip())),
                approved=body.approved,
                approved_by=user_id if body.approved else None,
                approved_at=now if body.approved else None,
                updated_at=now,
            )
            .returning(tenant_bid_content)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reusable content not found")
    return _content_response(row)


@router.delete("/library/{content_id}", status_code=204)
async def delete_bid_content(
    content_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> Response:
    result = await conn.execute(
        tenant_bid_content.delete().where(
            tenant_bid_content.c.id == content_id,
            tenant_bid_content.c.tenant_id == tenant_uuid(user),
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Reusable content not found")
    return Response(status_code=204)


@router.get("/process/{process_id}", response_model=ProposalWorkspaceResponse)
async def get_proposal(
    process_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> ProposalWorkspaceResponse:
    workspace = await _workspace(conn, process_id=process_id, tenant_id=tenant_uuid(user))
    return await _proposal_response(conn, workspace=workspace, tenant_id=tenant_uuid(user))


@router.post("/process/{process_id}/generate", response_model=ProposalWorkspaceResponse)
async def generate_proposal(
    process_id: uuid.UUID,
    body: ProposalGenerateRequest,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> ProposalWorkspaceResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace = await _workspace(conn, process_id=process_id, tenant_id=tenant_id)
    try:
        await consume_entitlement(
            conn,
            tenant_id=tenant_id,
            metric_code="proposal_drafts_month",
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
    requirement_query = sa.select(bid_requirements).where(
        bid_requirements.c.bid_workspace_id == workspace.id,
        bid_requirements.c.tenant_id == tenant_id,
    )
    if body.requirement_ids:
        requirement_query = requirement_query.where(
            bid_requirements.c.id.in_(body.requirement_ids)
        )
    requirements = (
        await conn.execute(
            requirement_query.order_by(
                bid_requirements.c.mandatory.desc(),
                bid_requirements.c.requirement_type,
                bid_requirements.c.created_at,
            )
        )
    ).all()
    if not requirements:
        raise HTTPException(status_code=409, detail="No bid requirements are available to draft")
    content_rows = (
        await conn.execute(
            sa.select(tenant_bid_content).where(
                tenant_bid_content.c.tenant_id == tenant_id,
                tenant_bid_content.c.approved.is_(True),
            )
        )
    ).all()
    reusable_library = [
        ReusableContent(
            id=str(item.id),
            title=item.title,
            body=item.body,
            tags=tuple(item.tags or []),
        )
        for item in content_rows
    ]

    for order, requirement in enumerate(requirements):
        evidence_document = None
        if requirement.evidence_document_id:
            evidence_document = (
                await conn.execute(
                    sa.select(documents).where(documents.c.id == requirement.evidence_document_id)
                )
            ).first()
        citations: list[dict[str, Any]] = []
        if requirement.evidence_document_id or requirement.source_excerpt:
            citations.append(
                {
                    "document_id": str(requirement.evidence_document_id)
                    if requirement.evidence_document_id
                    else None,
                    "document_title": evidence_document.title if evidence_document else None,
                    "source_url": evidence_document.source_url if evidence_document else None,
                    "page": requirement.evidence_page,
                    "excerpt": requirement.source_excerpt,
                }
            )
        requirement_text = " ".join(
            part for part in (requirement.title, requirement.description or "") if part
        )
        selected = select_reusable_content(requirement_text, reusable_library)
        prompt = proposal_prompt(
            requirement_title=requirement.title,
            requirement_description=requirement.description,
            evidence_excerpt=requirement.source_excerpt,
            reusable_content=selected,
            language=body.language,
        )
        if body.additional_instructions:
            prompt += f"\nAdditional user instructions:\n{body.additional_instructions}"
        llm_error = None
        try:
            drafted = await generate_text(
                http_client,
                instructions=(
                    "You draft public-procurement proposal answers. Never invent evidence. "
                    "Use [TODO] for unsupported facts and keep official evidence distinct "
                    "from reusable company content."
                ),
                input_text=prompt,
            )
        except (httpx.HTTPError, ValueError) as exc:
            drafted = None
            llm_error = type(exc).__name__
        draft = drafted or deterministic_first_draft(
            requirement_title=requirement.title,
            requirement_description=requirement.description,
            evidence_excerpt=requirement.source_excerpt,
            reusable_content=selected,
        )
        existing = (
            await conn.execute(
                sa.select(proposal_sections).where(
                    proposal_sections.c.bid_workspace_id == workspace.id,
                    proposal_sections.c.section_key == f"requirement:{requirement.id}",
                )
            )
        ).first()
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "llm_used": drafted is not None,
            "llm_error": llm_error,
            "reusable_content_ids": [item.id for item in selected],
            "grounded": True,
        }
        if existing is None:
            section_id = uuid.uuid4()
            await conn.execute(
                proposal_sections.insert().values(
                    id=section_id,
                    tenant_id=tenant_id,
                    bid_workspace_id=workspace.id,
                    requirement_id=requirement.id,
                    section_key=f"requirement:{requirement.id}",
                    title=requirement.title,
                    display_order=order,
                    status="DRAFT",
                    current_version=1,
                    body=draft,
                    citations=citations,
                    generation_metadata=metadata,
                    created_by=user_id,
                )
            )
            await _write_version(
                conn,
                tenant_id=tenant_id,
                section_id=section_id,
                version_number=1,
                body=draft,
                citations=citations,
                change_summary="Initial evidence-grounded draft",
                user_id=user_id,
            )
        else:
            version = existing.current_version + 1
            await _write_version(
                conn,
                tenant_id=tenant_id,
                section_id=existing.id,
                version_number=version,
                body=draft,
                citations=citations,
                change_summary="Regenerated from current requirement evidence",
                user_id=user_id,
            )
            await conn.execute(
                proposal_sections.update()
                .where(proposal_sections.c.id == existing.id)
                .values(
                    title=requirement.title,
                    display_order=order,
                    status="DRAFT",
                    current_version=version,
                    body=draft,
                    citations=citations,
                    generation_metadata=metadata,
                    updated_at=datetime.now(timezone.utc),
                )
            )
    return await _proposal_response(conn, workspace=workspace, tenant_id=tenant_id)


@router.patch("/sections/{section_id}", response_model=ProposalSectionResponse)
async def update_proposal_section(
    section_id: uuid.UUID,
    body: ProposalSectionUpdate,
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> ProposalSectionResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    current = (
        await conn.execute(
            sa.select(proposal_sections).where(
                proposal_sections.c.id == section_id,
                proposal_sections.c.tenant_id == tenant_id,
            )
        )
    ).first()
    if current is None:
        raise HTTPException(status_code=404, detail="Proposal section not found")
    if body.status and body.status not in _SECTION_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported proposal section status")
    values = body.model_dump(exclude_unset=True)
    change_summary = values.pop("change_summary", None)
    next_body = values.get("body", current.body)
    next_citations = list(current.citations or [])
    version = current.current_version + 1
    await _write_version(
        conn,
        tenant_id=tenant_id,
        section_id=section_id,
        version_number=version,
        body=next_body,
        citations=next_citations,
        change_summary=change_summary or "Manual review update",
        user_id=user_id,
    )
    values.update(current_version=version, updated_at=datetime.now(timezone.utc))
    row = (
        await conn.execute(
            proposal_sections.update()
            .where(proposal_sections.c.id == section_id)
            .values(**values)
            .returning(proposal_sections)
        )
    ).one()
    return _section_response(row)


@router.get(
    "/sections/{section_id}/versions",
    response_model=list[ProposalVersionResponse],
)
async def list_proposal_versions(
    section_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> list[ProposalVersionResponse]:
    exists = (
        await conn.execute(
            sa.select(proposal_sections.c.id).where(
                proposal_sections.c.id == section_id,
                proposal_sections.c.tenant_id == tenant_uuid(user),
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Proposal section not found")
    rows = (
        await conn.execute(
            sa.select(proposal_section_versions)
            .where(
                proposal_section_versions.c.proposal_section_id == section_id,
                proposal_section_versions.c.tenant_id == tenant_uuid(user),
            )
            .order_by(proposal_section_versions.c.version_number.desc())
        )
    ).all()
    return [
        ProposalVersionResponse(
            id=str(row.id),
            version_number=row.version_number,
            body=row.body,
            citations=list(row.citations or []),
            change_summary=row.change_summary,
            created_by=str(row.created_by),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/process/{process_id}/export.docx")
async def export_proposal_docx(
    process_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
) -> StreamingResponse:
    tenant_id = tenant_uuid(user)
    user_id = await ensure_workspace_user(conn, user)
    workspace = await _workspace(conn, process_id=process_id, tenant_id=tenant_id)
    rows = (
        await conn.execute(
            sa.select(proposal_sections)
            .where(
                proposal_sections.c.bid_workspace_id == workspace.id,
                proposal_sections.c.tenant_id == tenant_id,
            )
            .order_by(proposal_sections.c.display_order, proposal_sections.c.created_at)
        )
    ).mappings().all()
    if not rows:
        raise HTTPException(status_code=409, detail="Generate proposal sections before exporting")
    payload = render_proposal_docx(
        title=workspace.process_title or workspace.public_id or "Procintel proposal",
        sections=[dict(row) for row in rows],
    )
    safe_reference = re.sub(r"[^A-Za-z0-9_-]+", "-", workspace.public_id or str(process_id)).strip("-")
    file_name = f"proposal-{safe_reference or process_id}.docx"
    await conn.execute(
        proposal_exports.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            bid_workspace_id=workspace.id,
            format="DOCX",
            status="COMPLETED",
            file_name=file_name,
            manifest={
                "section_count": len(rows),
                "versions": {str(row["id"]): row["current_version"] for row in rows},
            },
            created_by=user_id,
            finished_at=datetime.now(timezone.utc),
        )
    )
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )

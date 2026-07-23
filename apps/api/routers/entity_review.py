"""Administrative fuzzy entity-resolution review and reversible merges."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import audit_log, entities, entity_match_candidates, entity_merge_log
from services.entity_resolution.candidates import generate_match_candidates

from ..auth import require_role
from ..db import get_tenant_scoped_conn
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/entity-review", tags=["entity-review"])
_REVIEW_ROLES = ("OWNER", "ADMIN")


class EntitySideResponse(BaseModel):
    id: str
    name: str
    entity_type: str
    status: str


class MatchCandidateResponse(BaseModel):
    id: str
    entity_a: EntitySideResponse
    entity_b: EntitySideResponse
    score: float
    score_breakdown: dict[str, Any]
    blocking_reason: str
    status: str
    review_notes: str | None
    created_at: datetime


class ReviewRequest(BaseModel):
    action: Literal["MERGE_A_INTO_B", "MERGE_B_INTO_A", "REJECT"]
    notes: str = Field(default="", max_length=4000)


class GenerationResponse(BaseModel):
    pairs_considered: int
    candidates_written: int
    identifier_conflicts: int


class MergeHistoryResponse(BaseModel):
    id: str
    surviving_entity_id: str
    merged_entity_id: str
    merge_reason: str
    evidence: dict[str, Any]
    performed_by: str
    performed_at: datetime
    reverted_at: datetime | None
    reverted_by: str | None


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid UUID") from exc


def _candidate_response(row: Any) -> MatchCandidateResponse:
    return MatchCandidateResponse(
        id=str(row.id),
        entity_a=EntitySideResponse(id=str(row.entity_a_id), name=row.name_a, entity_type=row.type_a, status=row.status_a),
        entity_b=EntitySideResponse(id=str(row.entity_b_id), name=row.name_b, entity_type=row.type_b, status=row.status_b),
        score=float(row.score), score_breakdown=row.score_breakdown,
        blocking_reason=row.blocking_reason, status=row.status,
        review_notes=row.review_notes, created_at=row.created_at,
    )


def _candidate_query() -> sa.Select:
    a = entities.alias("entity_a")
    b = entities.alias("entity_b")
    return (
        sa.select(
            entity_match_candidates,
            a.c.canonical_name.label("name_a"), a.c.entity_type.label("type_a"), a.c.status.label("status_a"),
            b.c.canonical_name.label("name_b"), b.c.entity_type.label("type_b"), b.c.status.label("status_b"),
        ).join(a, a.c.id == entity_match_candidates.c.entity_a_id)
        .join(b, b.c.id == entity_match_candidates.c.entity_b_id)
    )


@router.get("/candidates", response_model=list[MatchCandidateResponse])
async def list_candidates(
    status: str = "PENDING_REVIEW", limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    _: AuthenticatedUser = Depends(require_role(*_REVIEW_ROLES)),
) -> list[MatchCandidateResponse]:
    rows = (await conn.execute(
        _candidate_query()
        .where(entity_match_candidates.c.status == status)
        .order_by(entity_match_candidates.c.score.desc()).limit(limit)
    )).all()
    return [_candidate_response(row) for row in rows]


@router.post("/generate", response_model=GenerationResponse)
async def generate_candidates(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    _: AuthenticatedUser = Depends(require_role(*_REVIEW_ROLES)),
) -> GenerationResponse:
    result = await generate_match_candidates(conn)
    return GenerationResponse(**result.__dict__)


@router.post("/candidates/{candidate_id}/review", response_model=MatchCandidateResponse)
async def review_candidate(
    candidate_id: str, body: ReviewRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_REVIEW_ROLES)),
) -> MatchCandidateResponse:
    target = _uuid(candidate_id)
    candidate = (await conn.execute(sa.select(entity_match_candidates).where(entity_match_candidates.c.id == target).with_for_update())).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Match candidate not found")
    if candidate.status != "PENDING_REVIEW":
        raise HTTPException(status_code=409, detail="Candidate has already been reviewed")
    user_id = await ensure_workspace_user(conn, user)
    now = datetime.now(timezone.utc)
    if body.action == "REJECT":
        await conn.execute(entity_match_candidates.update().where(entity_match_candidates.c.id == target).values(
            status="REJECTED", reviewed_by=user_id, reviewed_at=now, review_notes=body.notes,
        ))
    else:
        if candidate.score_breakdown.get("identifier_conflict"):
            raise HTTPException(status_code=409, detail="Entities with conflicting valid AFM cannot be merged")
        surviving_id, merged_id = (
            (candidate.entity_b_id, candidate.entity_a_id)
            if body.action == "MERGE_A_INTO_B" else (candidate.entity_a_id, candidate.entity_b_id)
        )
        merged = (await conn.execute(sa.select(entities).where(entities.c.id == merged_id).with_for_update())).one()
        survivor = (await conn.execute(sa.select(entities).where(entities.c.id == surviving_id).with_for_update())).one()
        if merged.status != "ACTIVE" or survivor.status != "ACTIVE":
            raise HTTPException(status_code=409, detail="Both entities must be active")
        await conn.execute(entities.update().where(entities.c.id == merged_id).values(
            status="MERGED", merged_into_id=surviving_id, updated_at=now,
        ))
        await conn.execute(entity_merge_log.insert().values(
            id=uuid.uuid4(), surviving_entity_id=surviving_id, merged_entity_id=merged_id,
            match_candidate_id=target, merge_reason=body.notes or "Manual entity review",
            evidence={"score": float(candidate.score), "breakdown": candidate.score_breakdown},
            performed_by=user.email or user.subject,
        ))
        await conn.execute(entity_match_candidates.update().where(entity_match_candidates.c.id == target).values(
            status="MANUALLY_CONFIRMED", reviewed_by=user_id, reviewed_at=now, review_notes=body.notes,
        ))
        # §40.3 names "entity merge/split" explicitly as an audited action —
        # entity_merge_log already captures the merge-specific evidence
        # (score/breakdown/reason); this is the general cross-cutting trail
        # every other administrative action (exports, alert-rule changes,
        # workspace edits) already writes to.
        await conn.execute(audit_log.insert().values(
            id=uuid.uuid4(), tenant_id=tenant_uuid(user), actor_user_id=user_id,
            action="entity.merged", object_type="entities", object_id=surviving_id,
            details={"merged_entity_id": str(merged_id), "match_candidate_id": str(target), "notes": body.notes},
        ))
    updated = (await conn.execute(_candidate_query().where(entity_match_candidates.c.id == target))).one()
    return _candidate_response(updated)


@router.get("/merges", response_model=list[MergeHistoryResponse])
async def list_merges(
    limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    _: AuthenticatedUser = Depends(require_role(*_REVIEW_ROLES)),
) -> list[MergeHistoryResponse]:
    rows = (await conn.execute(sa.select(entity_merge_log).order_by(entity_merge_log.c.performed_at.desc()).limit(limit))).all()
    return [MergeHistoryResponse(
        id=str(row.id), surviving_entity_id=str(row.surviving_entity_id), merged_entity_id=str(row.merged_entity_id),
        merge_reason=row.merge_reason, evidence=row.evidence, performed_by=row.performed_by,
        performed_at=row.performed_at, reverted_at=row.reverted_at, reverted_by=row.reverted_by,
    ) for row in rows]


@router.post("/merges/{merge_id}/undo", response_model=MergeHistoryResponse)
async def undo_merge(
    merge_id: str,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_REVIEW_ROLES)),
) -> MergeHistoryResponse:
    target = _uuid(merge_id)
    row = (await conn.execute(sa.select(entity_merge_log).where(entity_merge_log.c.id == target).with_for_update())).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Merge not found")
    if row.reverted_at is not None:
        raise HTTPException(status_code=409, detail="Merge has already been reverted")
    now = datetime.now(timezone.utc)
    await conn.execute(entities.update().where(
        entities.c.id == row.merged_entity_id, entities.c.merged_into_id == row.surviving_entity_id,
    ).values(status="ACTIVE", merged_into_id=None, updated_at=now))
    await conn.execute(entity_merge_log.update().where(entity_merge_log.c.id == target).values(
        reverted_at=now, reverted_by=user.email or user.subject,
    ))
    if row.match_candidate_id:
        await conn.execute(entity_match_candidates.update().where(entity_match_candidates.c.id == row.match_candidate_id).values(
            status="MANUALLY_SPLIT", reviewed_at=now,
        ))
    user_id = await ensure_workspace_user(conn, user)
    await conn.execute(audit_log.insert().values(
        id=uuid.uuid4(), tenant_id=tenant_uuid(user), actor_user_id=user_id,
        action="entity.split", object_type="entities", object_id=row.merged_entity_id,
        details={"surviving_entity_id": str(row.surviving_entity_id), "merge_log_id": str(target)},
    ))
    updated = (await conn.execute(sa.select(entity_merge_log).where(entity_merge_log.c.id == target))).one()
    return MergeHistoryResponse(
        id=str(updated.id), surviving_entity_id=str(updated.surviving_entity_id), merged_entity_id=str(updated.merged_entity_id),
        merge_reason=updated.merge_reason, evidence=updated.evidence, performed_by=updated.performed_by,
        performed_at=updated.performed_at, reverted_at=updated.reverted_at, reverted_by=updated.reverted_by,
    )

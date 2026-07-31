"""Page-level document retrieval, cited Q&A and requirement extraction."""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    bid_requirements,
    bid_workspaces,
    document_comparisons,
    document_compliance_fields,
    document_pages,
    documents,
    procurement_acts,
)
from services.documents.intelligence import (
    PARSER_VERSION as STRUCTURED_PARSER_VERSION,
    compare_document_terms,
    extract_compliance_fields,
)
from services.intelligence.llm import generate_text

from ..auth import get_current_user, require_role
from ..db import get_conn, get_tenant_scoped_conn
from ..deps import get_http_client
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/document-intelligence", tags=["document-intelligence"])
_WRITE_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER")
_REQUIREMENT_PATTERN = re.compile(
    r"\b(απαιτ(?:εί|ούνται)|πρέπει\s+να|υποχρεούται|δικαιολογητικ|"
    r"πιστοποιητικ|εγγυητικ|προθεσμί|τεχνικ(?:ή|ές)\s+απαίτησ|"
    r"shall|required|must|certificate|deadline)\b",
    re.IGNORECASE,
)


class DocumentCitation(BaseModel):
    document_id: str
    document_title: str | None
    source_url: str | None
    page: int
    excerpt: str
    rank: float


class DocumentSearchResponse(BaseModel):
    query: str
    hits: list[DocumentCitation]


class DocumentQuestion(BaseModel):
    process_id: uuid.UUID
    question: str = Field(min_length=3, max_length=2000)


class DocumentAnswer(BaseModel):
    answer: str
    mode: str
    citations: list[DocumentCitation]
    limitations: str


class RequirementExtractionResponse(BaseModel):
    created: int
    skipped: int
    requirements: list[dict[str, Any]]


class ComplianceExtractionResponse(BaseModel):
    process_id: str
    fields_created: int
    fields: list[dict[str, Any]]
    parser_version: str


class DocumentComparisonRequest(BaseModel):
    base_document_id: uuid.UUID | None = None
    comparison_document_id: uuid.UUID | None = None
    comparison_type: str = Field(default="TERMS_AND_AMENDMENTS", max_length=80)


def _excerpt(text: str, query: str, *, width: int = 420) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= width:
        return compact
    terms = [term for term in re.findall(r"[\w\u0370-\u03ff]+", query.casefold()) if len(term) > 2]
    positions = [compact.casefold().find(term) for term in terms]
    found = [position for position in positions if position >= 0]
    center = min(found) if found else 0
    start = max(0, center - width // 3)
    end = min(len(compact), start + width)
    return f"{'…' if start else ''}{compact[start:end].strip()}{'…' if end < len(compact) else ''}"


async def _retrieve(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID,
    query: str,
    limit: int,
) -> list[DocumentCitation]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH ranked AS (
                    SELECT dp.document_id, dp.page_number, dp.text,
                           d.title, d.source_url,
                           ts_rank_cd(
                               dp.text_search,
                               websearch_to_tsquery('simple', :query)
                           ) AS rank
                    FROM document_pages dp
                    JOIN documents d ON d.id = dp.document_id
                    JOIN procurement_acts a ON a.id = d.act_id
                    WHERE a.process_id = :process_id
                      AND (
                          dp.text_search @@ websearch_to_tsquery('simple', :query)
                          OR dp.text ILIKE :fallback
                      )
                )
                SELECT * FROM ranked
                ORDER BY rank DESC, page_number
                LIMIT :limit
                """
            ),
            {
                "process_id": process_id,
                "query": query,
                "fallback": f"%{query.strip()}%",
                "limit": limit,
            },
        )
    ).mappings().all()
    return [
        DocumentCitation(
            document_id=str(row["document_id"]),
            document_title=row["title"],
            source_url=row["source_url"],
            page=row["page_number"],
            excerpt=_excerpt(row["text"], query),
            rank=float(row["rank"] or 0),
        )
        for row in rows
    ]


@router.get("/search", response_model=DocumentSearchResponse)
async def search_document_pages(
    process_id: uuid.UUID,
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=12, ge=1, le=50),
    conn: AsyncConnection = Depends(get_conn),
    _: AuthenticatedUser = Depends(get_current_user),
) -> DocumentSearchResponse:
    hits = await _retrieve(conn, process_id=process_id, query=q, limit=limit)
    return DocumentSearchResponse(query=q, hits=hits)


@router.post("/ask", response_model=DocumentAnswer)
async def ask_documents(
    body: DocumentQuestion,
    conn: AsyncConnection = Depends(get_conn),
    http_client: httpx.AsyncClient = Depends(get_http_client),
    _: AuthenticatedUser = Depends(get_current_user),
) -> DocumentAnswer:
    citations = await _retrieve(conn, process_id=body.process_id, query=body.question, limit=8)
    if not citations:
        return DocumentAnswer(
            answer="Δεν βρέθηκε σχετικό απόσπασμα στα επεξεργασμένα έγγραφα αυτής της διαδικασίας.",
            mode="NO_EVIDENCE",
            citations=[],
            limitations="Η απάντηση περιορίζεται στα αρχεία που έχουν ανακτηθεί και ολοκληρώσει OCR ή text extraction.",
        )
    context = "\n\n".join(
        f"[{index}] {citation.document_title or 'Έγγραφο'}, σελίδα {citation.page}\n{citation.excerpt}"
        for index, citation in enumerate(citations, start=1)
    )
    answer: str | None = None
    try:
        answer = await generate_text(
            http_client,
            instructions=(
                "Απάντησε στα ελληνικά ως αναλυτής δημοσίων συμβάσεων. "
                "Χρησιμοποίησε αποκλειστικά το παρεχόμενο evidence. "
                "Κάθε ουσιαστικός ισχυρισμός πρέπει να παραπέμπει σε [n]. "
                "Αν το evidence δεν επαρκεί, δήλωσέ το καθαρά."
            ),
            input_text=f"Ερώτηση:\n{body.question}\n\nEvidence:\n{context}",
        )
    except (httpx.HTTPError, ValueError):
        answer = None
    if answer is None:
        answer = (
            "Στα διαθέσιμα έγγραφα εντοπίστηκαν τα ακόλουθα σχετικά σημεία:\n\n"
            + "\n\n".join(
                f"[{index}] {citation.excerpt}"
                for index, citation in enumerate(citations[:3], start=1)
            )
        )
    return DocumentAnswer(
        answer=answer,
        mode="LLM_GROUNDED" if answer and not answer.startswith("Στα διαθέσιμα") else "EXTRACTIVE",
        citations=citations,
        limitations="Δεν αποτελεί νομική γνωμοδότηση. Επαληθεύστε τις απαιτήσεις στο επίσημο έγγραφο και τη συγκεκριμένη σελίδα.",
    )


def _requirement_type(text: str) -> str:
    normalized = text.casefold()
    if any(term in normalized for term in ("πιστοποιη", "δικαιολογη", "εγγυητικ")):
        return "CERTIFICATE"
    if any(term in normalized for term in ("τεχνικ", "προδιαγραφ", "technical")):
        return "TECHNICAL"
    if any(term in normalized for term in ("οικονομ", "τιμή", "financial")):
        return "FINANCIAL"
    if any(term in normalized for term in ("προθεσμ", "deadline")):
        return "DEADLINE"
    if any(term in normalized for term in ("νομ", "law", "legal")):
        return "LEGAL"
    return "ELIGIBILITY"


def extract_requirement_candidates(pages: list[dict[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in pages:
        text = re.sub(r"\s+", " ", str(page["text"])).strip()
        for sentence in re.split(r"(?<=[.;:])\s+|\n+", text):
            sentence = sentence.strip(" -•\t")
            if not 30 <= len(sentence) <= 900 or not _REQUIREMENT_PATTERN.search(sentence):
                continue
            key = sentence.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "title": sentence[:240],
                    "description": sentence if len(sentence) > 240 else None,
                    "requirement_type": _requirement_type(sentence),
                    "evidence_document_id": page["document_id"],
                    "evidence_page": page["page_number"],
                    "source_excerpt": sentence[:800],
                }
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


async def _process_pages(
    conn: AsyncConnection,
    process_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    statement = (
        sa.select(
            document_pages.c.document_id,
            document_pages.c.page_number,
            document_pages.c.text,
        )
        .join(documents, documents.c.id == document_pages.c.document_id)
        .join(procurement_acts, procurement_acts.c.id == documents.c.act_id)
        .where(procurement_acts.c.process_id == process_id)
        .order_by(document_pages.c.document_id, document_pages.c.page_number)
    )
    if document_ids:
        statement = statement.where(document_pages.c.document_id.in_(document_ids))
    return [dict(row) for row in (await conn.execute(statement)).mappings().all()]


@router.get("/{process_id}/compliance", response_model=list[dict[str, Any]])
async def list_compliance_fields(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_conn),
    _: AuthenticatedUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            sa.select(document_compliance_fields)
            .where(document_compliance_fields.c.process_id == process_id)
            .order_by(
                document_compliance_fields.c.category,
                document_compliance_fields.c.field_name,
                document_compliance_fields.c.page_number,
            )
        )
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/{process_id}/extract-compliance", response_model=ComplianceExtractionResponse)
async def extract_structured_compliance(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_conn),
    _: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> ComplianceExtractionResponse:
    pages = await _process_pages(conn, process_id)
    if not pages:
        raise HTTPException(status_code=404, detail="no extracted document pages found for this process")
    extracted = extract_compliance_fields(pages)
    created = 0
    for field in extracted:
        result = await conn.execute(
            pg_insert(document_compliance_fields)
            .values(
                id=uuid.uuid4(),
                process_id=process_id,
                document_id=field.document_id,
                page_number=field.page_number,
                category=field.category,
                field_name=field.field_name,
                value=field.value,
                source_excerpt=field.source_excerpt,
                extraction_method=field.extraction_method,
                parser_version=STRUCTURED_PARSER_VERSION,
                confidence=field.confidence,
            )
            .on_conflict_do_nothing()
        )
        created += int(result.rowcount or 0)
    await conn.commit()
    fields = await list_compliance_fields(process_id, conn, _)
    return ComplianceExtractionResponse(
        process_id=str(process_id),
        fields_created=created,
        fields=fields,
        parser_version=STRUCTURED_PARSER_VERSION,
    )


@router.get("/{process_id}/comparisons", response_model=list[dict[str, Any]])
async def list_document_comparisons(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_conn),
    _: AuthenticatedUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            sa.select(document_comparisons)
            .where(document_comparisons.c.process_id == process_id)
            .order_by(document_comparisons.c.created_at.desc())
        )
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/{process_id}/compare", response_model=dict[str, Any])
async def compare_process_documents(
    process_id: uuid.UUID,
    body: DocumentComparisonRequest,
    conn: AsyncConnection = Depends(get_conn),
    _: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> dict[str, Any]:
    selected_ids = [value for value in (body.base_document_id, body.comparison_document_id) if value]
    if len(selected_ids) != 2:
        selected_ids = list(
            (
                await conn.execute(
                    sa.text(
                        """
                        SELECT d.id
                        FROM documents d
                        JOIN procurement_acts a ON a.id = d.act_id
                        WHERE a.process_id = :process_id
                          AND EXISTS (SELECT 1 FROM document_pages p WHERE p.document_id = d.id)
                        ORDER BY d.created_at DESC
                        LIMIT 2
                        """
                    ),
                    {"process_id": process_id},
                )
            ).scalars()
        )
        selected_ids.reverse()
    if len(selected_ids) != 2 or selected_ids[0] == selected_ids[1]:
        raise HTTPException(status_code=409, detail="two distinct extracted documents are required")
    pages = await _process_pages(conn, process_id, selected_ids)
    by_document: dict[uuid.UUID, list[dict[str, Any]]] = {selected_ids[0]: [], selected_ids[1]: []}
    for page in pages:
        by_document[page["document_id"]].append(page)
    if not all(by_document.values()):
        raise HTTPException(status_code=422, detail="both documents must belong to the process and have extracted pages")
    result = compare_document_terms(by_document[selected_ids[0]], by_document[selected_ids[1]])
    comparison_id = uuid.uuid4()
    comparison_type = body.comparison_type.upper()
    inserted = (
        await conn.execute(
            pg_insert(document_comparisons)
            .values(
                id=comparison_id,
                process_id=process_id,
                base_document_id=selected_ids[0],
                comparison_document_id=selected_ids[1],
                comparison_type=comparison_type,
                summary=result["summary"],
                changes={"counts": result["counts"], "items": result["changes"]},
                parser_version=STRUCTURED_PARSER_VERSION,
            )
            .on_conflict_do_update(
                index_elements=[
                    document_comparisons.c.base_document_id,
                    document_comparisons.c.comparison_document_id,
                    document_comparisons.c.comparison_type,
                    document_comparisons.c.parser_version,
                ],
                set_={
                    "summary": result["summary"],
                    "changes": {"counts": result["counts"], "items": result["changes"]},
                    "created_at": sa.func.now(),
                },
            )
            .returning(document_comparisons)
        )
    ).mappings().one()
    await conn.commit()
    return dict(inserted)


@router.post("/{process_id}/extract-requirements", response_model=RequirementExtractionResponse)
async def extract_bid_requirements(
    process_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_WRITE_ROLES)),
) -> RequirementExtractionResponse:
    tenant_id = tenant_uuid(user)
    workspace_id = (
        await conn.execute(
            sa.select(bid_workspaces.c.id).where(
                bid_workspaces.c.tenant_id == tenant_id,
                bid_workspaces.c.process_id == process_id,
            )
        )
    ).scalar_one_or_none()
    if workspace_id is None:
        raise HTTPException(status_code=409, detail="start the bid workspace before extracting requirements")
    pages = (
        await conn.execute(
            sa.text(
                """
                SELECT dp.document_id, dp.page_number, dp.text
                FROM document_pages dp
                JOIN documents d ON d.id = dp.document_id
                JOIN procurement_acts a ON a.id = d.act_id
                WHERE a.process_id = :process_id AND length(dp.text) > 20
                ORDER BY dp.document_id, dp.page_number
                """
            ),
            {"process_id": process_id},
        )
    ).mappings().all()
    candidates = extract_requirement_candidates([dict(page) for page in pages])
    existing = {
        value.casefold()
        for value in (
            await conn.execute(
                sa.select(bid_requirements.c.title).where(
                    bid_requirements.c.bid_workspace_id == workspace_id,
                    bid_requirements.c.tenant_id == tenant_id,
                )
            )
        ).scalars()
    }
    user_id = await ensure_workspace_user(conn, user)
    created: list[dict[str, Any]] = []
    skipped = 0
    for candidate in candidates:
        if candidate["title"].casefold() in existing:
            skipped += 1
            continue
        requirement_id = uuid.uuid4()
        await conn.execute(
            bid_requirements.insert().values(
                id=requirement_id,
                bid_workspace_id=workspace_id,
                tenant_id=tenant_id,
                created_by=user_id,
                **candidate,
            )
        )
        existing.add(candidate["title"].casefold())
        created.append({"id": str(requirement_id), **{key: str(value) if isinstance(value, uuid.UUID) else value for key, value in candidate.items()}})
    await conn.commit()
    return RequirementExtractionResponse(created=len(created), skipped=skipped, requirements=created)

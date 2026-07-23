"""Idempotent canonical writes for the documents pipeline (§23, §24).

Dedup key is `documents.sha256` (unique index, `db/migrations/04_*.sql`) —
the same file downloaded twice (same content, possibly a different URL)
resolves to the same `documents` row rather than a duplicate, mirroring
the `content_sha256` dedup pattern every ingestion connector already uses
on `source_records`.

`field_provenance` rows are append-only here (never updated in place):
each pipeline run over the same document writes a fresh provenance row per
extracted field, `observed_at` marking when. This matches
`field_provenance`'s own shape (no unique constraint forcing one row per
field) — the evidence drawer (§30.4) shows history, not just the latest
value.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import document_pages, documents, field_provenance


@dataclass(frozen=True)
class DocumentUpsertResult:
    document_id: uuid.UUID
    is_new: bool


async def upsert_document(
    conn: AsyncConnection,
    *,
    sha256: str,
    object_uri: str,
    mime_type: str | None,
    file_size: int,
    act_id: uuid.UUID | None,
    source_record_id: uuid.UUID | None,
    document_type: str | None,
    title: str | None,
    source_url: str | None,
) -> DocumentUpsertResult:
    existing = await conn.execute(select(documents.c.id).where(documents.c.sha256 == sha256))
    row = existing.first()
    if row is not None:
        return DocumentUpsertResult(document_id=row.id, is_new=False)

    document_id = uuid.uuid4()
    await conn.execute(
        documents.insert().values(
            id=document_id,
            act_id=act_id,
            source_record_id=source_record_id,
            document_type=document_type,
            title=title,
            source_url=source_url,
            object_uri=object_uri,
            mime_type=mime_type,
            file_size=file_size,
            sha256=sha256,
            text_extraction_status="PENDING",
        )
    )
    return DocumentUpsertResult(document_id=document_id, is_new=True)


async def update_document_extraction_status(
    conn: AsyncConnection,
    *,
    document_id: uuid.UUID,
    text_extraction_status: str,
    page_count: int | None,
    language: str | None,
) -> None:
    await conn.execute(
        documents.update()
        .where(documents.c.id == document_id)
        .values(text_extraction_status=text_extraction_status, page_count=page_count, language=language)
    )


@dataclass(frozen=True)
class PageWrite:
    page_number: int
    text: str
    extraction_method: str  # TEXT_LAYER | OCR
    ocr_mean_confidence: float | None = None


async def write_document_pages(conn: AsyncConnection, *, document_id: uuid.UUID, pages: list[PageWrite]) -> None:
    """Delete-and-reinsert per document — same simplest-correct approach
    `act_cpv_codes`/`act_locations` use in `connectors/khmdhs/db_writer.py`
    for per-parent-row collections; revisit only if per-page history turns
    out to matter."""
    await conn.execute(document_pages.delete().where(document_pages.c.document_id == document_id))
    for page in pages:
        await conn.execute(
            document_pages.insert().values(
                id=uuid.uuid4(),
                document_id=document_id,
                page_number=page.page_number,
                text=page.text,
                extraction_method=page.extraction_method,
                ocr_mean_confidence=page.ocr_mean_confidence,
            )
        )


async def write_field_provenance(
    conn: AsyncConnection,
    *,
    object_type: str,
    object_id: uuid.UUID,
    field_name: str,
    source_record_id: uuid.UUID,
    source_path: str | None,
    extraction_method: str,
    confidence: float,
    value: Any,
    observed_at: datetime | None = None,
) -> uuid.UUID:
    provenance_id = uuid.uuid4()
    value_hash = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    await conn.execute(
        field_provenance.insert().values(
            id=provenance_id,
            object_type=object_type,
            object_id=object_id,
            field_name=field_name,
            source_record_id=source_record_id,
            source_path=source_path,
            extraction_method=extraction_method,
            confidence=confidence,
            observed_at=observed_at or datetime.now(timezone.utc),
            value_hash=value_hash,
        )
    )
    return provenance_id

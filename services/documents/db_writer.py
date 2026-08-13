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

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    document_act_links,
    document_pages,
    documents,
    field_provenance,
)


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
    document_id = uuid.uuid4()
    inserted = (
        await conn.execute(
            pg_insert(documents)
            .values(
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
            .on_conflict_do_nothing(index_elements=[documents.c.sha256])
            .returning(documents.c.id)
        )
    ).scalar_one_or_none()
    is_new = inserted is not None
    if inserted is None:
        document_id = (
            await conn.execute(
                sa.select(documents.c.id).where(documents.c.sha256 == sha256)
            )
        ).scalar_one()
        if act_id is not None:
            await conn.execute(
                documents.update()
                .where(documents.c.id == document_id)
                .values(
                    act_id=sa.func.coalesce(documents.c.act_id, act_id),
                    source_record_id=sa.func.coalesce(
                        documents.c.source_record_id,
                        source_record_id,
                    ),
                    document_type=sa.func.coalesce(documents.c.document_type, document_type),
                    title=sa.func.coalesce(documents.c.title, title),
                    source_url=sa.func.coalesce(documents.c.source_url, source_url),
                )
            )

    if act_id is not None:
        link_insert = pg_insert(document_act_links).values(
            document_id=document_id,
            act_id=act_id,
            source_record_id=source_record_id,
            document_type=document_type,
            title=title,
            source_url=source_url,
        )
        await conn.execute(
            link_insert.on_conflict_do_update(
                index_elements=[
                    document_act_links.c.document_id,
                    document_act_links.c.act_id,
                ],
                set_={
                    "source_record_id": sa.func.coalesce(
                        link_insert.excluded.source_record_id,
                        document_act_links.c.source_record_id,
                    ),
                    "document_type": sa.func.coalesce(
                        link_insert.excluded.document_type,
                        document_act_links.c.document_type,
                    ),
                    "title": sa.func.coalesce(
                        link_insert.excluded.title,
                        document_act_links.c.title,
                    ),
                    "source_url": sa.func.coalesce(
                        link_insert.excluded.source_url,
                        document_act_links.c.source_url,
                    ),
                },
            )
        )
    return DocumentUpsertResult(document_id=document_id, is_new=is_new)


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

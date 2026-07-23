"""ΚΗΜΔΗΣ attachment discovery and document-pipeline integration."""

from __future__ import annotations

import os
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import documents, procurement_acts
from services.documents.pipeline import ProcessDocumentResult, process_document
from services.documents.storage import LocalFilesystemDocumentBlobStore
from services.intelligence.tender_brief import khmdhs_attachment_url

from .client import ALL_RESOURCES


def khmdhs_document_type(resource: str) -> str:
    if resource not in ALL_RESOURCES:
        raise ValueError(f"unknown KHMDHS resource: {resource!r}")
    return f"KHMDHS_{resource.upper()}_PDF"


async def has_khmdhs_attachment(
    conn: AsyncConnection,
    *,
    act_id: uuid.UUID,
    resource: str,
) -> bool:
    document_type = khmdhs_document_type(resource)
    return (
        await conn.execute(
            select(documents.c.id)
            .where(
                documents.c.act_id == act_id,
                documents.c.document_type == document_type,
            )
            .limit(1)
        )
    ).first() is not None


async def process_khmdhs_attachment(
    conn: AsyncConnection,
    *,
    resource: str,
    adam: str,
    act_id: uuid.UUID,
    title: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ProcessDocumentResult | None:
    """Download one provider-owned PDF once, then run extraction/OCR."""
    if await has_khmdhs_attachment(conn, act_id=act_id, resource=resource):
        return None
    if title is None:
        title = (
            await conn.execute(
                select(procurement_acts.c.title).where(procurement_acts.c.id == act_id)
            )
        ).scalar()
    return await process_document(
        conn,
        url=khmdhs_attachment_url(resource, adam),
        act_id=act_id,
        document_type=khmdhs_document_type(resource),
        title=title or adam,
        http_client=http_client,
        blob_store=LocalFilesystemDocumentBlobStore(
            os.environ.get("DOCUMENT_STORE_ROOT", "./data/documents")
        ),
    )

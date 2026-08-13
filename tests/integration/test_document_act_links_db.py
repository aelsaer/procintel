from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    document_act_links,
    documents,
    procurement_acts,
    source_records,
)
from services.documents.db_writer import upsert_document

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_one_deduplicated_document_can_link_to_multiple_acts():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    act_ids = (uuid.uuid4(), uuid.uuid4())
    content_hash = uuid.uuid4().hex * 2
    try:
        async with engine.connect() as conn:
            await conn.execute(
                source_records.insert().values(
                    id=source_id,
                    source_system="DOCUMENT_LINK_TEST",
                    resource_type="PDF",
                    source_native_id=str(source_id),
                    content_sha256=uuid.uuid4().hex * 2,
                    payload_uri="s3://test/source.json",
                    fetched_at=datetime.now(timezone.utc),
                    parse_status="PARSED",
                )
            )
            for act_id in act_ids:
                await conn.execute(
                    procurement_acts.insert().values(
                        id=act_id,
                        act_type="OTHER",
                        title=f"Document link {act_id}",
                        source_record_id=source_id,
                    )
                )

            first = await upsert_document(
                conn,
                sha256=content_hash,
                object_uri="s3://private-bucket/documents/shared.pdf",
                mime_type="application/pdf",
                file_size=10,
                act_id=act_ids[0],
                source_record_id=source_id,
                document_type="TENDER_DOC",
                title="Shared tender document",
                source_url="https://example.test/first.pdf",
            )
            second = await upsert_document(
                conn,
                sha256=content_hash,
                object_uri="s3://private-bucket/documents/shared.pdf",
                mime_type="application/pdf",
                file_size=10,
                act_id=act_ids[1],
                source_record_id=source_id,
                document_type="TENDER_DOC",
                title="Shared tender document",
                source_url="https://example.test/second.pdf",
            )
            await conn.commit()

            assert first.is_new is True
            assert second.is_new is False
            assert second.document_id == first.document_id
            links = (
                await conn.execute(
                    sa.select(document_act_links).where(
                        document_act_links.c.document_id == first.document_id
                    )
                )
            ).all()
            assert {link.act_id for link in links} == set(act_ids)
            assert {link.source_url for link in links} == {
                "https://example.test/first.pdf",
                "https://example.test/second.pdf",
            }

            await conn.execute(
                document_act_links.delete().where(
                    document_act_links.c.document_id == first.document_id
                )
            )
            await conn.execute(documents.delete().where(documents.c.id == first.document_id))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id.in_(act_ids)))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
            await conn.commit()
    finally:
        await engine.dispose()

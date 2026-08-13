"""Full documents pipeline (§23/§24) against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Downloads a small
hand-built, fully-ASCII PDF fixture (`identifiers_sample.pdf` — see
tests/fixtures/documents/, generated to avoid the Type1/Helvetica
Greek-glyph-encoding complexity a Greek-text fixture would need) over a
respx-mocked URL, runs it through `process_document` end to end, and
confirms: a `documents` row and its owning `source_records` row exist, one
`document_pages` row with the exact text-layer content, and
`field_provenance` rows for the amount/ΑΦΜ/CPV/ΑΔΑΜ the fixture's text
contains. Then re-processes the same URL/content and confirms the second
call is deduped (no new document, no duplicate field_provenance rows) —
the idempotency contract `db_writer.py`'s module docstring promises.
"""

import os
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import document_pages, documents, field_provenance, source_records
from services.documents.config import DocumentPipelineConfig
from services.documents.pipeline import process_document
from services.documents.storage import LocalFilesystemDocumentBlobStore

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "documents" / "identifiers_sample.pdf"
FIXTURE_BYTES = FIXTURE_PATH.read_bytes()
URL = "https://example.test/identifiers_sample.pdf"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_process_document_writes_documents_pages_and_provenance_and_is_idempotent(tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, content=FIXTURE_BYTES))
    config = DocumentPipelineConfig(allow_test_hosts=True)
    blob_store = LocalFilesystemDocumentBlobStore(tmp_path / "documents")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            result = await process_document(conn, url=URL, document_type="TENDER_DOC", config=config, blob_store=blob_store)
            await conn.commit()

            assert result.is_new is True
            assert result.page_count == 1
            assert len(result.amounts) == 1

            document_row = (
                await conn.execute(select(documents).where(documents.c.id == result.document_id))
            ).one()
            assert document_row.mime_type == "application/pdf"
            assert document_row.text_extraction_status == "TEXT_LAYER"
            assert document_row.page_count == 1
            assert document_row.source_record_id is not None

            source_record_row = (
                await conn.execute(select(source_records).where(source_records.c.id == document_row.source_record_id))
            ).one()
            assert source_record_row.source_system == "DOCUMENTS"
            assert source_record_row.resource_type == "TENDER_DOC"

            pages = (
                await conn.execute(select(document_pages).where(document_pages.c.document_id == result.document_id))
            ).all()
            assert len(pages) == 1
            assert pages[0].extraction_method == "TEXT_LAYER"
            assert "090000045" in pages[0].text

            provenance_rows = (
                await conn.execute(
                    select(field_provenance).where(
                        field_provenance.c.object_type == "documents",
                        field_provenance.c.object_id == result.document_id,
                    )
                )
            ).all()
            field_names = {row.field_name for row in provenance_rows}
            assert field_names == {"amount", "afm", "cpv", "adam"}
            afm_row = next(row for row in provenance_rows if row.field_name == "afm")
            assert float(afm_row.confidence) == pytest.approx(0.95)
            assert afm_row.source_path == "page:1"
            assert afm_row.source_record_id == document_row.source_record_id

            first_provenance_count = len(provenance_rows)

            # Re-processing the exact same content is a no-op: deduped by
            # sha256, no new document/pages/provenance rows.
            result_again = await process_document(
                conn, url=URL, document_type="TENDER_DOC", config=config, blob_store=blob_store
            )
            await conn.commit()
            assert result_again.is_new is False
            assert result_again.document_id == result.document_id

            provenance_rows_after = (
                await conn.execute(
                    select(field_provenance).where(
                        field_provenance.c.object_type == "documents",
                        field_provenance.c.object_id == result.document_id,
                    )
                )
            ).all()
            assert len(provenance_rows_after) == first_provenance_count
    finally:
        await engine.dispose()


@respx.mock
async def test_process_document_retains_official_pdf_when_extraction_page_limit_is_exceeded(tmp_path):
    page_limit_fixture = FIXTURE_BYTES + b"\n% page-limit retention test\n"
    respx.get(URL).mock(return_value=httpx.Response(200, content=page_limit_fixture))
    blob_store = LocalFilesystemDocumentBlobStore(tmp_path / "documents")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            result = await process_document(
                conn,
                url=URL,
                document_type="PAGE_LIMIT_TEST",
                config=DocumentPipelineConfig(max_pages=0, allow_test_hosts=True),
                blob_store=blob_store,
            )
            await conn.commit()

            document_row = (
                await conn.execute(select(documents).where(documents.c.id == result.document_id))
            ).one()
            assert result.is_new is True
            assert result.page_count == 1
            assert document_row.text_extraction_status == "SKIPPED_PAGE_LIMIT"
            assert document_row.page_count == 1
            assert document_row.source_record_id is not None
            assert not (
                await conn.execute(
                    select(document_pages).where(document_pages.c.document_id == result.document_id)
                )
            ).all()
    finally:
        await engine.dispose()


@respx.mock
async def test_process_document_skips_only_pages_that_exceed_the_pixel_limit(tmp_path):
    pixel_limit_fixture = FIXTURE_BYTES + b"\n% pixel-limit retention test\n"
    respx.get(URL).mock(return_value=httpx.Response(200, content=pixel_limit_fixture))
    blob_store = LocalFilesystemDocumentBlobStore(tmp_path / "documents")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            result = await process_document(
                conn,
                url=URL,
                document_type="PIXEL_LIMIT_TEST",
                config=DocumentPipelineConfig(
                    allow_test_hosts=True,
                    min_text_layer_chars_per_page=10_000,
                    max_page_pixels=1,
                ),
                blob_store=blob_store,
            )
            await conn.commit()

            document_row = (
                await conn.execute(select(documents).where(documents.c.id == result.document_id))
            ).one()
            pages = (
                await conn.execute(
                    select(document_pages).where(document_pages.c.document_id == result.document_id)
                )
            ).all()
            assert result.is_new is True
            assert document_row.text_extraction_status == "PARTIAL_PIXEL_LIMIT"
            assert [(page.page_number, page.extraction_method, page.text) for page in pages] == [
                (1, "SKIPPED_PIXEL_LIMIT", ""),
            ]
    finally:
        await engine.dispose()

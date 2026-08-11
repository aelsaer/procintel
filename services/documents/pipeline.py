"""End-to-end document pipeline orchestration (§23.1's full flow: download
-> MIME validation -> SHA-256 -> antivirus scan -> store original ->
text-layer detection -> extraction -> OCR only when required -> page
segmentation -> entity/field extraction -> indexing -> evidence
references).

`process_document` is the one entrypoint every caller (CLI, or a future
"process this act's attachments" hook from another connector) uses.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    document_compliance_fields,
    documents,
    procurement_acts,
    source_records,
)
from packages.source_clients.rate_limit import RateLimiter
from services.competitors.participation import record_document_participant

from .amounts import ExtractedAmount, extract_amounts
from .antivirus import AntivirusScanner, configured_antivirus_scanner
from .config import DocumentPipelineConfig
from .db_writer import PageWrite, write_document_pages, write_field_provenance, upsert_document, update_document_extraction_status
from .download import download_document
from .entities import (
    extract_ada,
    extract_adam,
    extract_afm,
    extract_cpv,
    extract_dates,
    extract_duration,
    extract_iban,
    extract_lot_numbers,
    extract_mis,
    extract_protocol_numbers,
    extract_procurement_participants,
    extract_unit_quantities,
)
from .mime import sniff_mime_type
from .intelligence import PARSER_VERSION, extract_compliance_fields
from .ocr import run_ocr
from .pdf_text import PdfPageLimitExceededError, extract_text_layer, open_pdf, rasterize_page
from .storage import DocumentBlobStore, LocalFilesystemDocumentBlobStore


class UnsupportedMimeTypeError(Exception):
    def __init__(self, sniffed: str | None, allowed: frozenset[str]) -> None:
        super().__init__(f"sniffed MIME type {sniffed!r} is not one of {sorted(allowed)}")
        self.sniffed = sniffed


class VirusDetectedError(Exception):
    def __init__(self, signature: str | None) -> None:
        super().__init__(f"antivirus scan flagged this document: {signature!r}")
        self.signature = signature


@dataclass(frozen=True)
class ProcessDocumentResult:
    document_id: uuid.UUID
    is_new: bool  # False if this exact content (by sha256) was already processed — extraction was skipped
    page_count: int
    amounts: list[ExtractedAmount] = field(default_factory=list)


async def _ensure_document_source_record(
    conn: AsyncConnection, *, sha256: str, url: str, document_type: str, http_status: int
) -> uuid.UUID:
    """Mirrors the connector `content_sha256` dedup pattern
    (`connectors/khmdhs/db_writer.py::ingest_khmdhs_record`), except it
    always returns a usable id (existing or freshly inserted) since
    `field_provenance.source_record_id` is NOT NULL — every extracted
    field needs one to point at, even on the rare path where a prior run
    wrote the `source_records` row but crashed before the `documents` row."""
    existing = await conn.execute(
        select(source_records.c.id).where(
            source_records.c.source_system == "DOCUMENTS",
            source_records.c.resource_type == document_type,
            source_records.c.content_sha256 == sha256,
        )
    )
    row = existing.first()
    if row is not None:
        return row.id

    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="DOCUMENTS",
            resource_type=document_type,
            source_native_id=url,
            content_sha256=sha256,
            payload_uri=url,
            fetched_at=datetime.now(timezone.utc),
            http_status=http_status,
            parse_status="PARSED",
        )
    )
    return source_record_id


async def process_document(
    conn: AsyncConnection,
    *,
    url: str,
    act_id: uuid.UUID | None = None,
    document_type: str = "GENERIC",
    title: str | None = None,
    config: DocumentPipelineConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
    download_rate_limiter: RateLimiter | None = None,
    blob_store: DocumentBlobStore | None = None,
    av_scanner: AntivirusScanner | None = None,
) -> ProcessDocumentResult:
    config = config or DocumentPipelineConfig()
    blob_store = blob_store or LocalFilesystemDocumentBlobStore(
        root=os.environ.get("DOCUMENT_STORE_ROOT", "./data/documents")
    )
    av_scanner = av_scanner or configured_antivirus_scanner()

    downloaded = await download_document(
        url,
        config=config,
        http_client=http_client,
        rate_limiter=download_rate_limiter,
    )
    sha256 = hashlib.sha256(downloaded.payload).hexdigest()

    sniffed = sniff_mime_type(downloaded.payload)
    if sniffed not in config.allowed_mime_types:
        raise UnsupportedMimeTypeError(sniffed, config.allowed_mime_types)

    scan_result = await av_scanner.scan(downloaded.payload)
    if not scan_result.is_clean:
        raise VirusDetectedError(scan_result.signature)

    blob_ref = await blob_store.put(payload=downloaded.payload, mime_type=sniffed)

    upsert_result = await upsert_document(
        conn,
        sha256=sha256,
        object_uri=blob_ref.object_uri,
        mime_type=sniffed,
        file_size=blob_ref.size_bytes,
        act_id=act_id,
        source_record_id=None,
        document_type=document_type,
        title=title,
        source_url=url,
    )
    if not upsert_result.is_new:
        # Same content already fully processed — skip re-running OCR/extraction.
        return ProcessDocumentResult(document_id=upsert_result.document_id, is_new=False, page_count=0)

    source_record_id = await _ensure_document_source_record(
        conn,
        sha256=sha256,
        url=url,
        document_type=document_type,
        http_status=downloaded.http_status,
    )
    await conn.execute(
        documents.update().where(documents.c.id == upsert_result.document_id).values(source_record_id=source_record_id)
    )

    pdf_document = open_pdf(downloaded.payload)
    try:
        text_layer_pages = extract_text_layer(pdf_document, config=config)
    except PdfPageLimitExceededError as exc:
        # The page cap protects extraction/OCR resources; it must not hide an
        # otherwise valid official document from the product and evidence UI.
        pdf_document.close()
        await update_document_extraction_status(
            conn,
            document_id=upsert_result.document_id,
            text_extraction_status="SKIPPED_PAGE_LIMIT",
            page_count=exc.page_count,
            language=None,
        )
        return ProcessDocumentResult(
            document_id=upsert_result.document_id,
            is_new=True,
            page_count=exc.page_count,
        )

    page_writes: list[PageWrite] = []
    any_ocr = False
    all_amounts: list[ExtractedAmount] = []

    for page_content in text_layer_pages:
        if page_content.has_usable_text_layer:
            page_text = page_content.text_layer
            extraction_method = "TEXT_LAYER"
            ocr_mean_confidence = None
            field_confidence_scale = 1.0
        else:
            any_ocr = True
            image = rasterize_page(pdf_document, page_number=page_content.page_number, config=config)
            ocr_result = await run_ocr(image, config=config)
            page_text = ocr_result.text
            extraction_method = "OCR"
            ocr_mean_confidence = ocr_result.mean_confidence
            field_confidence_scale = (ocr_result.mean_confidence / 100.0) if ocr_result.mean_confidence else 0.5

        page_writes.append(
            PageWrite(
                page_number=page_content.page_number,
                text=page_text,
                extraction_method=extraction_method,
                ocr_mean_confidence=ocr_mean_confidence,
            )
        )

        page_amounts = extract_amounts(page_text)
        all_amounts.extend(page_amounts)
        source_path = f"page:{page_content.page_number}"

        for amount in page_amounts:
            await write_field_provenance(
                conn,
                object_type="documents",
                object_id=upsert_result.document_id,
                field_name="amount",
                source_record_id=source_record_id,
                source_path=source_path,
                extraction_method="REGEX",
                confidence=round(amount.parser_confidence * field_confidence_scale, 4),
                value=f"{amount.normalized_amount} {amount.currency}",
            )

        for extractor, field_name, value_fn, base_confidence in (
            (extract_ada, "ada", lambda r: r.normalized_value, 0.9),
            (extract_adam, "adam", lambda r: r.normalized_value, 0.9),
            (extract_afm, "afm", lambda r: r.raw_value, 0.95),
            (extract_cpv, "cpv", lambda r: r.raw_value, 0.9),
            (extract_mis, "mis_ops", lambda r: r.raw_value, 0.7),
            (extract_dates, "date", lambda r: r.raw_value, 0.85),
            (extract_protocol_numbers, "protocol_number", lambda r: r.raw_value, 0.75),
            (extract_duration, "duration", lambda r: f"{r.quantity} {r.unit}", 0.85),
            (extract_lot_numbers, "lot_number", lambda r: r.lot_number, 0.85),
            (extract_unit_quantities, "unit_quantity", lambda r: f"{r.quantity} {r.unit}", 0.7),
        ):
            for result in extractor(page_text):
                confidence = base_confidence * field_confidence_scale
                # ΑΦΜ with a failed checksum: still recorded (§7.2 — never
                # rejected outright), but at a visibly lower confidence.
                if field_name == "afm" and not result.checksum_valid:
                    confidence *= 0.5
                await write_field_provenance(
                    conn,
                    object_type="documents",
                    object_id=upsert_result.document_id,
                    field_name=field_name,
                    source_record_id=source_record_id,
                    source_path=source_path,
                    extraction_method="REGEX",
                    confidence=round(confidence, 4),
                    value=value_fn(result),
                )

        if act_id is not None:
            for participant in extract_procurement_participants(page_text):
                await write_field_provenance(
                    conn,
                    object_type="documents",
                    object_id=upsert_result.document_id,
                    field_name=f"participant_{participant.role.lower()}",
                    source_record_id=source_record_id,
                    source_path=source_path,
                    extraction_method="REGEX",
                    confidence=round(participant.confidence * field_confidence_scale, 4),
                    value=f"{participant.name}|{participant.afm}",
                )
                await record_document_participant(
                    conn,
                    act_id=act_id,
                    document_id=upsert_result.document_id,
                    source_record_id=source_record_id,
                    source_page=page_content.page_number,
                    participant=participant,
                    confidence_scale=field_confidence_scale,
                )

        if config.extract_iban:
            for result in extract_iban(page_text):
                await write_field_provenance(
                    conn,
                    object_type="documents",
                    object_id=upsert_result.document_id,
                    field_name="iban",
                    source_record_id=source_record_id,
                    source_path=source_path,
                    extraction_method="REGEX",
                    confidence=round((0.9 if result.checksum_valid else 0.3) * field_confidence_scale, 4),
                    value=result.normalized_value,
                )

    await write_document_pages(conn, document_id=upsert_result.document_id, pages=page_writes)
    if act_id is not None:
        process_id = (
            await conn.execute(
                select(procurement_acts.c.process_id).where(
                    procurement_acts.c.id == act_id
                )
            )
        ).scalar()
        if process_id is not None:
            structured_fields = extract_compliance_fields(
                [
                    {
                        "document_id": upsert_result.document_id,
                        "page_number": page.page_number,
                        "text": page.text,
                    }
                    for page in page_writes
                ]
            )
            for field in structured_fields:
                await conn.execute(
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
                        parser_version=PARSER_VERSION,
                        confidence=field.confidence,
                    )
                    .on_conflict_do_nothing()
                )
    await update_document_extraction_status(
        conn,
        document_id=upsert_result.document_id,
        text_extraction_status="OCR_DONE" if any_ocr else "TEXT_LAYER",
        page_count=len(page_writes),
        language=None,
    )

    return ProcessDocumentResult(
        document_id=upsert_result.document_id,
        is_new=True,
        page_count=len(page_writes),
        amounts=all_amounts,
    )

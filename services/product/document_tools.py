"""Pure helpers for phrase monitoring and practical document exports."""

from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass(frozen=True)
class PhraseMatch:
    matched_phrases: tuple[str, ...]
    page_numbers: tuple[int, ...]
    excerpts: tuple[dict[str, str | int], ...]


def normalize_search_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(character for character in decomposed if not unicodedata.combining(character)).split()
    )


def match_document_phrases(
    page_texts: list[tuple[int, str]],
    phrases: list[str],
    *,
    match_mode: str,
) -> PhraseMatch | None:
    cleaned = list(dict.fromkeys(phrase.strip() for phrase in phrases if phrase.strip()))
    normalized_phrases = {phrase: normalize_search_text(phrase) for phrase in cleaned}
    hits: dict[str, list[tuple[int, str]]] = {phrase: [] for phrase in cleaned}
    for page_number, page_text in page_texts:
        normalized_page = normalize_search_text(page_text)
        for phrase, normalized_phrase in normalized_phrases.items():
            if normalized_phrase and normalized_phrase in normalized_page:
                position = normalized_page.find(normalized_phrase)
                start = max(0, position - 100)
                end = min(len(page_text), position + len(phrase) + 180)
                hits[phrase].append((page_number, page_text[start:end].strip()))
    matched = [phrase for phrase in cleaned if hits[phrase]]
    mode = match_mode.upper()
    if mode == "ALL" and len(matched) != len(cleaned):
        return None
    if not matched:
        return None
    excerpts = tuple(
        {"phrase": phrase, "page": page, "excerpt": excerpt}
        for phrase in matched
        for page, excerpt in hits[phrase][:3]
    )
    return PhraseMatch(
        matched_phrases=tuple(matched),
        page_numbers=tuple(sorted({int(item["page"]) for item in excerpts})),
        excerpts=excerpts,
    )


def safe_archive_name(value: str, *, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9Α-Ωα-ω._-]+", "-", value).strip("-.")
    return name[:120] or fallback


def build_document_archive(
    files: list[tuple[str, bytes]],
    *,
    manifest: dict,
) -> bytes:
    output = io.BytesIO()
    seen: dict[str, int] = {}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files:
            count = seen.get(name, 0)
            seen[name] = count + 1
            if count:
                stem, dot, suffix = name.rpartition(".")
                name = f"{stem or suffix}-{count + 1}{dot}{suffix if dot else ''}"
            archive.writestr(name, payload)
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
    return output.getvalue()


def render_editable_document_docx(
    *,
    title: str,
    pages: list[tuple[int, str]],
    source_url: str | None,
) -> bytes:
    document = ET.Element(f"{{{_W}}}document")
    body = ET.SubElement(document, f"{{{_W}}}body")

    def paragraph(text: str, *, bold: bool = False) -> None:
        node = ET.SubElement(body, f"{{{_W}}}p")
        run = ET.SubElement(node, f"{{{_W}}}r")
        if bold:
            properties = ET.SubElement(run, f"{{{_W}}}rPr")
            ET.SubElement(properties, f"{{{_W}}}b")
        text_node = ET.SubElement(run, f"{{{_W}}}t")
        text_node.text = text

    paragraph(title, bold=True)
    paragraph(f"Editable text conversion · {datetime.now(timezone.utc).isoformat()}")
    if source_url:
        paragraph(f"Official source: {source_url}")
    paragraph("Formatting may differ from the original PDF. Verify against the official document.")
    for page_number, text in pages:
        paragraph(f"Page {page_number}", bold=True)
        for line in text.splitlines() or [""]:
            paragraph(line)
    ET.SubElement(body, f"{{{_W}}}sectPr")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            ET.tostring(document, encoding="utf-8", xml_declaration=True),
        )
    return output.getvalue()


async def evaluate_phrase_monitor(
    conn: AsyncConnection,
    *,
    monitor_id: uuid.UUID,
) -> int:
    from packages.domain.tables import (
        act_cpv_codes,
        document_pages,
        document_phrase_matches,
        document_phrase_monitors,
        documents,
        procurement_acts,
    )

    monitor = (
        await conn.execute(
            sa.select(document_phrase_monitors).where(
                document_phrase_monitors.c.id == monitor_id
            )
        )
    ).first()
    if monitor is None or not monitor.is_active:
        return 0
    query = (
        sa.select(
            documents.c.id.label("document_id"),
            procurement_acts.c.process_id,
            document_pages.c.page_number,
            document_pages.c.text,
        )
        .join(documents, documents.c.id == document_pages.c.document_id)
        .outerjoin(procurement_acts, procurement_acts.c.id == documents.c.act_id)
        .where(document_pages.c.text != "")
    )
    cpv_prefixes = list(monitor.cpv_prefixes or [])
    if cpv_prefixes:
        related_act = procurement_acts.alias("monitor_related_act")
        related_cpv = act_cpv_codes.alias("monitor_related_cpv")
        query = query.where(
            sa.exists(
                sa.select(1)
                .select_from(
                    related_act.join(
                        related_cpv,
                        related_cpv.c.act_id == related_act.c.id,
                    )
                )
                .where(
                    related_act.c.process_id == procurement_acts.c.process_id,
                    sa.or_(
                        *(
                            related_cpv.c.cpv_code.like(f"{prefix}%")
                            for prefix in cpv_prefixes
                        )
                    ),
                )
            )
        )
    page_rows = (await conn.execute(query)).all()
    grouped: dict[uuid.UUID, dict[str, object]] = {}
    for row in page_rows:
        entry = grouped.setdefault(
            row.document_id,
            {"process_id": row.process_id, "pages": []},
        )
        pages = entry["pages"]
        assert isinstance(pages, list)
        pages.append((row.page_number, row.text))

    matched_document_ids: list[uuid.UUID] = []
    for document_id, entry in grouped.items():
        pages = entry["pages"]
        assert isinstance(pages, list)
        match = match_document_phrases(
            pages,
            list(monitor.phrases or []),
            match_mode=monitor.match_mode,
        )
        if match is None:
            continue
        matched_document_ids.append(document_id)
        statement = pg_insert(document_phrase_matches).values(
            id=uuid.uuid4(),
            tenant_id=monitor.tenant_id,
            monitor_id=monitor.id,
            document_id=document_id,
            process_id=entry["process_id"],
            matched_phrases=list(match.matched_phrases),
            page_numbers=list(match.page_numbers),
            excerpts=list(match.excerpts),
            matched_at=datetime.now(timezone.utc),
        )
        await conn.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    document_phrase_matches.c.monitor_id,
                    document_phrase_matches.c.document_id,
                ],
                set_={
                    "process_id": statement.excluded.process_id,
                    "matched_phrases": statement.excluded.matched_phrases,
                    "page_numbers": statement.excluded.page_numbers,
                    "excerpts": statement.excluded.excerpts,
                    "matched_at": statement.excluded.matched_at,
                },
            )
        )
    stale = document_phrase_matches.delete().where(
        document_phrase_matches.c.monitor_id == monitor.id
    )
    if matched_document_ids:
        stale = stale.where(
            document_phrase_matches.c.document_id.not_in(matched_document_ids)
        )
    await conn.execute(stale)
    return len(matched_document_ids)


async def evaluate_all_phrase_monitors(conn: AsyncConnection) -> dict[str, int]:
    from packages.domain.tables import document_phrase_monitors

    monitor_ids = list(
        (
            await conn.execute(
                sa.select(document_phrase_monitors.c.id).where(
                    document_phrase_monitors.c.is_active.is_(True)
                )
            )
        ).scalars()
    )
    total_matches = 0
    for monitor_id in monitor_ids:
        total_matches += await evaluate_phrase_monitor(conn, monitor_id=monitor_id)
    return {"monitors": len(monitor_ids), "matches": total_matches}

"""Asynchronous CSV/XLSX export generation with no provider calls."""

from __future__ import annotations

import csv
import io
import os
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from packages.domain.tables import export_jobs
from services.search_index.lexical import query_concept_pattern


def _async_url(value: str) -> str:
    return "postgresql+asyncpg://" + value.removeprefix("postgresql://") if value.startswith("postgresql://") else value


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def _filter_values(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    return [str(item).strip() for item in values if str(item).strip()]


def _scope_params(filters: dict[str, Any]) -> dict[str, Any]:
    cpv_values = _filter_values(filters.get("cpv_prefix")) + _filter_values(filters.get("cpv_prefixes"))
    keyword_values = _filter_values(filters.get("keyword")) + _filter_values(filters.get("keywords"))
    cpv_likes = [f"{value.split('-', 1)[0][:8]}%" for value in dict.fromkeys(cpv_values)]
    keyword_patterns = [
        pattern
        for value in dict.fromkeys(keyword_values)
        if (pattern := query_concept_pattern(value)) is not None
    ]
    taxonomy_match_mode = str(filters.get("taxonomy_match") or "ANY").upper()
    if taxonomy_match_mode == "KEYWORD_REQUIRED" and keyword_patterns:
        cpv_likes = []
    taxonomy_match_all = (
        taxonomy_match_mode in {"ALL", "CPV_AND_KEYWORD"}
        or filters.get("taxonomy_match_any") is False
    )
    municipality = str(filters.get("municipality") or "").strip()
    nuts_code = str(filters.get("nuts_code") or "").strip().upper()
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    amount_min = filters.get("amount_min")
    return {
        "cpv_likes": cpv_likes,
        "keyword_patterns": keyword_patterns,
        "taxonomy_match_mode": taxonomy_match_mode,
        "taxonomy_match_all": taxonomy_match_all,
        "date_from": date.fromisoformat(date_from) if isinstance(date_from, str) and date_from else date_from,
        "date_to": date.fromisoformat(date_to) if isinstance(date_to, str) and date_to else date_to,
        "nuts": nuts_code or None,
        "municipality_like": f"%{municipality}%" if municipality else None,
        "amount_min": Decimal(str(amount_min)) if amount_min is not None else None,
    }


def _write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _cell(row.get(column)) for column in columns})


def _write_xlsx(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    all_rows = [columns, *[[_cell(row.get(column)) for column in columns] for row in rows]]
    xml_rows: list[str] = []
    for row_index, values in enumerate(all_rows, start=1):
        cells = []
        for column_index, value in enumerate(values, start=1):
            number = column_index
            letters = ""
            while number:
                number, remainder = divmod(number - 1, 26)
                letters = chr(65 + remainder) + letters
            cells.append(f'<c r="{letters}{row_index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    worksheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Procintel export" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


async def _export_rows(conn: AsyncConnection, *, tenant_id: uuid.UUID, export_type: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    scope_params = _scope_params(filters)
    if export_type == "PIPELINE":
        result = await conn.execute(sa.text(
            """
            SELECT p.id, p.stage, p.priority, p.expected_value, p.next_action, p.due_at,
                   pp.id AS process_id, pp.title, pp.lifecycle_status, p.updated_at
            FROM opportunity_pipeline_items p JOIN procurement_processes pp ON pp.id=p.process_id
            WHERE p.tenant_id=CAST(:tenant_id AS uuid) ORDER BY p.updated_at DESC
            """
        ), {"tenant_id": str(tenant_id)})
    elif export_type in {"SUPPLIERS", "BUYERS"}:
        roles = "('SUPPLIER','CONTRACTOR')" if export_type == "SUPPLIERS" else "('BUYER','CONTRACTING_AUTHORITY')"
        result = await conn.execute(sa.text(
            f"""
            SELECT e.id, e.canonical_name, MAX(i.value_normalized) FILTER (WHERE i.scheme='AFM') AS afm,
                   COUNT(DISTINCT p.act_id) AS acts, SUM(COALESCE(p.amount,a.amount_net)) AS recorded_value,
                   'KHMDHS/DIAVGEIA/GEMI/TED/MEF' AS source_attribution
            FROM act_parties p JOIN entities e ON e.id=p.entity_id JOIN procurement_acts a ON a.id=p.act_id
            LEFT JOIN entity_identifiers i ON i.entity_id=e.id AND i.is_current
            WHERE p.party_role IN {roles}
            GROUP BY e.id, e.canonical_name ORDER BY recorded_value DESC NULLS LAST
            """
        ))
    elif export_type == "RELATIONSHIPS":
        result = await conn.execute(sa.text(
            """
            SELECT pp.id AS process_id, pp.title AS process,
                   buyer.canonical_name AS buyer,
                   supplier.canonical_name AS supplier,
                   MAX(COALESCE(sp.amount,a.amount_net)) AS recorded_value,
                   STRING_AGG(DISTINCT cpv.cpv_code, ', ') AS cpv_codes,
                   'PROCURES/AWARDED_TO' AS relationship,
                   'act_parties/procurement_processes' AS source_attribution
            FROM procurement_processes pp
            JOIN procurement_acts a ON a.process_id=pp.id AND a.is_current=TRUE
            LEFT JOIN act_parties bp ON bp.act_id=a.id AND bp.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
            LEFT JOIN entities buyer ON buyer.id=COALESCE(bp.entity_id,pp.buyer_entity_id)
            LEFT JOIN act_parties sp ON sp.act_id=a.id AND sp.party_role IN ('SUPPLIER','CONTRACTOR')
            LEFT JOIN entities supplier ON supplier.id=sp.entity_id
            LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id
            WHERE procintel_taxonomy_match(
                a.id,
                a.title,
                CAST(:cpv_likes AS TEXT[]),
                CAST(:keyword_patterns AS TEXT[]),
                CAST(:taxonomy_match_all AS BOOLEAN)
            )
              AND (CAST(:date_from AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) >= CAST(:date_from AS date))
              AND (CAST(:date_to AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) <= CAST(:date_to AS date))
              AND (
                  CAST(:nuts AS text) IS NULL
                  OR EXISTS (
                      SELECT 1 FROM act_locations scope_location
                      WHERE scope_location.act_id = a.id
                        AND scope_location.nuts_code LIKE CAST(:nuts AS text) || '%'
                  )
              )
              AND (
                  CAST(:municipality_like AS text) IS NULL
                  OR EXISTS (
                      SELECT 1 FROM act_locations scope_location
                      WHERE scope_location.act_id = a.id
                        AND (
                            scope_location.municipality_name ILIKE CAST(:municipality_like AS text)
                            OR scope_location.place_text ILIKE CAST(:municipality_like AS text)
                        )
                  )
              )
              AND (CAST(:amount_min AS numeric) IS NULL OR COALESCE(sp.amount,a.amount_gross,a.amount_net,pp.estimated_value,0) >= CAST(:amount_min AS numeric))
            GROUP BY pp.id, pp.title, buyer.canonical_name, supplier.canonical_name
            ORDER BY recorded_value DESC NULLS LAST
            """
        ), scope_params)
    elif export_type == "OPPORTUNITIES":
        result = await conn.execute(sa.text(
            """
            SELECT pp.id AS process_id, pp.title, pp.lifecycle_status, pp.estimated_value,
                   buyer.canonical_name AS buyer, MAX(a.end_date) AS deadline,
                   STRING_AGG(DISTINCT cpv.cpv_code, ', ') AS cpv_codes,
                   STRING_AGG(DISTINCT COALESCE(l.municipality_name,l.place_text,l.region_name), ', ') AS locations,
                   'KHMDHS/DIAVGEIA/TED' AS source_attribution
            FROM procurement_processes pp JOIN procurement_acts a ON a.process_id=pp.id AND a.is_current=TRUE
            LEFT JOIN entities buyer ON buyer.id=pp.buyer_entity_id LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id
            LEFT JOIN act_locations l ON l.act_id=a.id
            WHERE a.act_type IN ('REQUEST','NOTICE')
              AND procintel_taxonomy_match(
                  a.id,
                  a.title,
                  CAST(:cpv_likes AS TEXT[]),
                  CAST(:keyword_patterns AS TEXT[]),
                  CAST(:taxonomy_match_all AS BOOLEAN)
              )
              AND (CAST(:date_from AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) >= CAST(:date_from AS date))
              AND (CAST(:date_to AS date) IS NULL OR COALESCE(a.publication_date,a.decision_date,a.submission_date) <= CAST(:date_to AS date))
              AND (CAST(:nuts AS text) IS NULL OR l.nuts_code LIKE CAST(:nuts AS text) || '%')
              AND (
                  CAST(:municipality_like AS text) IS NULL
                  OR l.municipality_name ILIKE CAST(:municipality_like AS text)
                  OR l.place_text ILIKE CAST(:municipality_like AS text)
              )
              AND (CAST(:amount_min AS numeric) IS NULL OR COALESCE(a.amount_gross,a.amount_net,pp.estimated_value,0) >= CAST(:amount_min AS numeric))
            GROUP BY pp.id, pp.title, pp.lifecycle_status, pp.estimated_value, buyer.canonical_name
            ORDER BY MAX(COALESCE(a.publication_date,a.submission_date,a.decision_date)) DESC NULLS LAST
            """
        ), scope_params)
    else:
        raise ValueError(f"Unsupported export type: {export_type}")
    return [dict(row) for row in result.mappings().all()]


async def process_export_job(conn: AsyncConnection, job_id: uuid.UUID) -> None:
    job = (await conn.execute(sa.select(export_jobs).where(export_jobs.c.id == job_id).with_for_update())).first()
    if job is None or job.status not in {"PENDING", "FAILED"}:
        return
    now = datetime.now(timezone.utc)
    await conn.execute(export_jobs.update().where(export_jobs.c.id == job_id).values(status="RUNNING", started_at=now, error=None))
    await conn.commit()
    try:
        rows = await _export_rows(conn, tenant_id=job.tenant_id, export_type=job.export_type, filters=job.filters or {})
        columns = list(rows[0]) if rows else ["no_results"]
        root = Path(os.environ.get("EXPORT_ROOT", "raw/exports")).resolve()
        target_dir = root / str(job.tenant_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = job.format.lower()
        file_name = f"procintel-{job.export_type.lower()}-{now:%Y%m%d-%H%M%S}-{job.id}.{suffix}"
        path = target_dir / file_name
        if job.format == "CSV":
            _write_csv(path, columns, rows)
            mime_type = "text/csv; charset=utf-8"
        else:
            _write_xlsx(path, columns, rows)
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        await conn.execute(export_jobs.update().where(export_jobs.c.id == job_id).values(
            status="SUCCEEDED", row_count=len(rows), file_name=file_name, mime_type=mime_type,
            storage_path=str(path), finished_at=datetime.now(timezone.utc), expires_at=now + timedelta(days=7),
        ))
        await conn.commit()
    except Exception as exc:
        await conn.execute(export_jobs.update().where(export_jobs.c.id == job_id).values(
            status="FAILED", error={"message": str(exc)}, finished_at=datetime.now(timezone.utc),
        ))
        await conn.commit()
        raise


async def process_export_job_by_id(job_id: uuid.UUID) -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(_async_url(database_url))
    try:
        async with engine.connect() as conn:
            await process_export_job(conn, job_id)
    finally:
        await engine.dispose()

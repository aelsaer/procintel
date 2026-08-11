"""Database-backed geospatial enrichment queue and worker."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_locations,
    document_pages,
    documents,
    geocoding_cache,
    geospatial_enrichment_jobs,
    postal_code_nuts,
    procurement_acts,
    source_records,
)

from .config import GeocoderConfig
from .extract import (
    AdminUnit,
    LocationCandidate,
    extract_location_candidates,
    has_explicit_foreign_performance,
    normalize_place,
)
from .geonames import (
    GazetteerPlace,
    build_gazetteer_alias_index,
    load_gazetteer_places,
    match_indexed_gazetteer_place,
)
from .geocoder import GeocodeResult, NominatimGeocoder, match_local_boundary

ELIGIBLE_ACT_TYPES = ("REQUEST", "APPROVED_REQUEST", "NOTICE", "AWARD", "CONTRACT", "TED_NOTICE")
MAX_DOCUMENT_TEXT_CHARS = 200_000
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    act_id: uuid.UUID
    source_record_id: uuid.UUID
    attempt_count: int


@dataclass(frozen=True)
class JobResult:
    job_id: uuid.UUID
    status: str
    candidates_found: int
    locations_written: int
    geocoded_locations: int


def _cache_key(candidate: LocationCandidate) -> tuple[str, str]:
    normalized = "|".join(
        (
            normalize_place(candidate.place_text),
            candidate.postal_code or "",
            ",".join(candidate.nuts_codes),
            "GR",
        )
    )
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def load_admin_units(conn: AsyncConnection) -> list[AdminUnit]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT boundary_type, code, name, nuts_code,
                       ST_Y(ST_PointOnSurface(geom)) AS latitude,
                       ST_X(ST_PointOnSurface(geom)) AS longitude
                FROM administrative_boundaries
                WHERE name IS NOT NULL
                  AND boundary_type IN ('MUNICIPALITY', 'REGIONAL_UNIT', 'PREFECTURE', 'REGION')
                """
            )
        )
    ).all()
    return [
        AdminUnit(
            boundary_type=row.boundary_type,
            code=row.code,
            name=row.name,
            nuts_code=row.nuts_code,
            latitude=row.latitude,
            longitude=row.longitude,
        )
        for row in rows
    ]


def _raw_payload_paths(payload_uri: str) -> list[Path]:
    if payload_uri.startswith("file://"):
        payload_uri = payload_uri[7:]
    if "://" in payload_uri:
        return []
    path = Path(payload_uri)
    candidates = [path]
    raw_root_value = os.environ.get("RAW_STORE_ROOT", "").strip()
    if raw_root_value:
        raw_root = Path(raw_root_value)
        parts = path.parts
        if not path.is_absolute():
            candidates.append(raw_root / path)
        raw_component_indexes = [
            index
            for index, part in enumerate(parts)
            if part == raw_root.name
        ]
        if raw_component_indexes:
            suffix = parts[raw_component_indexes[-1] + 1 :]
            candidates.append(raw_root.joinpath(*suffix))
    return list(dict.fromkeys(candidates))


def _read_raw_payload(payload_uri: str) -> tuple[dict[str, Any], str | None]:
    paths = _raw_payload_paths(payload_uri)
    if not paths:
        return {}, f"unsupported raw object URI: {payload_uri.split('://', 1)[0]}"
    path = next((candidate for candidate in paths if candidate.exists()), None)
    if path is None:
        attempted = ", ".join(str(candidate) for candidate in paths)
        return {}, f"raw payload not found: {payload_uri} (attempted: {attempted})"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, f"raw payload could not be read: {exc}"
    return (payload if isinstance(payload, dict) else {}), None


async def _document_texts(conn: AsyncConnection, act_id: uuid.UUID) -> list[str]:
    rows = (
        await conn.execute(
            sa.select(document_pages.c.text)
            .select_from(document_pages.join(documents, documents.c.id == document_pages.c.document_id))
            .where(documents.c.act_id == act_id)
            .order_by(document_pages.c.page_number)
        )
    ).all()
    remaining = MAX_DOCUMENT_TEXT_CHARS
    texts: list[str] = []
    for row in rows:
        if remaining <= 0:
            break
        text = (row.text or "")[:remaining]
        if text:
            texts.append(text)
            remaining -= len(text)
    return texts


async def _cached_result(
    conn: AsyncConnection,
    provider: str,
    query_hash: str,
    *,
    confidence_cap: float,
) -> tuple[bool, GeocodeResult | None]:
    row = (
        await conn.execute(
            sa.select(geocoding_cache).where(
                geocoding_cache.c.provider == provider,
                geocoding_cache.c.query_hash == query_hash,
            )
        )
    ).first()
    if row is None:
        return False, None
    await conn.execute(
        geocoding_cache.update()
        .where(geocoding_cache.c.id == row.id)
        .values(hit_count=row.hit_count + 1, last_used_at=datetime.now(timezone.utc))
    )
    if row.status != "FOUND" or row.latitude is None or row.longitude is None:
        return True, None
    return True, GeocodeResult(
        latitude=row.latitude,
        longitude=row.longitude,
        display_name=row.display_name or row.query_normalized,
        municipality_name=row.municipality_name,
        regional_unit_name=row.regional_unit_name,
        region_name=row.region_name,
        postal_code=row.postal_code,
        country_code=row.country_code or "GR",
        precision=row.precision or "POINT",
        provider=row.provider,
        confidence=min(0.9, confidence_cap),
        raw_response=row.raw_response,
    )


async def _store_cache(
    conn: AsyncConnection,
    *,
    provider: str,
    normalized_query: str,
    query_hash: str,
    result: GeocodeResult | None,
) -> None:
    values = {
        "id": uuid.uuid4(),
        "provider": provider,
        "query_hash": query_hash,
        "query_normalized": normalized_query,
        "status": "FOUND" if result else "NOT_FOUND",
        "latitude": result.latitude if result else None,
        "longitude": result.longitude if result else None,
        "display_name": result.display_name if result else None,
        "municipality_name": result.municipality_name if result else None,
        "regional_unit_name": result.regional_unit_name if result else None,
        "region_name": result.region_name if result else None,
        "postal_code": result.postal_code if result else None,
        "country_code": result.country_code if result else "GR",
        "precision": result.precision if result else None,
        "raw_response": result.raw_response if result else None,
        "created_at": datetime.now(timezone.utc),
        "last_used_at": datetime.now(timezone.utc),
    }
    await conn.execute(
        pg_insert(geocoding_cache)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[geocoding_cache.c.provider, geocoding_cache.c.query_hash],
            set_={key: value for key, value in values.items() if key not in {"id", "provider", "query_hash", "created_at"}},
        )
    )


async def _resolve_candidate(
    conn: AsyncConnection,
    candidate: LocationCandidate,
    *,
    admin_units: Sequence[AdminUnit],
    gazetteer_places: Sequence[GazetteerPlace],
    gazetteer_alias_index: Mapping[str, Sequence[GazetteerPlace]] | None,
    remote: NominatimGeocoder | None,
) -> GeocodeResult | None:
    if candidate.extraction_method == "NUTS_CODE" or candidate.granularity_hint == "POSTAL_CODE":
        local = match_local_boundary(candidate, admin_units)
        if local:
            return local
    gazetteer = match_indexed_gazetteer_place(
        candidate,
        gazetteer_places,
        gazetteer_alias_index or {},
    )
    if gazetteer:
        return gazetteer
    local = match_local_boundary(candidate, admin_units)
    if local:
        return local
    if remote is None:
        return None

    normalized_query, query_hash = _cache_key(candidate)
    cached, cached_result = await _cached_result(
        conn,
        remote.config.provider_name,
        query_hash,
        confidence_cap=candidate.confidence,
    )
    if cached:
        return cached_result
    result = await remote.geocode(candidate)
    await _store_cache(
        conn,
        provider=remote.config.provider_name,
        normalized_query=normalized_query,
        query_hash=query_hash,
        result=result,
    )
    return result


async def _attach_postal_nuts(
    conn: AsyncConnection,
    candidates: Sequence[LocationCandidate],
) -> list[LocationCandidate]:
    postal_codes = sorted(
        {candidate.postal_code for candidate in candidates if candidate.postal_code}
    )
    if not postal_codes:
        return list(candidates)
    rows = (
        await conn.execute(
            sa.select(
                postal_code_nuts.c.postal_code,
                postal_code_nuts.c.nuts_code,
            ).where(
                postal_code_nuts.c.country_code == "GR",
                postal_code_nuts.c.postal_code.in_(postal_codes),
            )
        )
    ).all()
    by_postal: dict[str, list[str]] = {}
    for row in rows:
        by_postal.setdefault(row.postal_code, []).append(row.nuts_code)
    return [
        replace(
            candidate,
            nuts_codes=tuple(
                dict.fromkeys(
                    (
                        *candidate.nuts_codes,
                        *by_postal.get(candidate.postal_code or "", []),
                    )
                )
            ),
        )
        for candidate in candidates
    ]


def _location_names(candidate: LocationCandidate, result: GeocodeResult | None) -> tuple[str | None, str | None, str | None]:
    if result:
        return result.municipality_name, result.regional_unit_name, result.region_name
    if candidate.granularity_hint == "MUNICIPALITY":
        return candidate.place_text, None, None
    if candidate.granularity_hint in {"REGIONAL_UNIT", "PREFECTURE"}:
        return None, candidate.place_text, None
    if candidate.granularity_hint == "REGION":
        return None, None, candidate.place_text
    return candidate.place_text, None, None


async def _write_locations(
    conn: AsyncConnection,
    *,
    job: ClaimedJob,
    candidates: Sequence[LocationCandidate],
    resolved: Sequence[GeocodeResult | None],
    raw_warning: str | None,
) -> int:
    await conn.execute(
        act_locations.delete().where(
            act_locations.c.act_id == job.act_id,
            act_locations.c.enrichment_job_id.is_not(None),
        )
    )
    written = 0
    seen: set[tuple[str, str | None, float | None, float | None]] = set()
    for candidate, geocode in zip(candidates, resolved, strict=True):
        municipality, regional_unit, region = _location_names(candidate, geocode)
        latitude = geocode.latitude if geocode else None
        longitude = geocode.longitude if geocode else None
        key = (normalize_place(candidate.place_text), candidate.postal_code, latitude, longitude)
        if key in seen:
            continue
        seen.add(key)
        evidence = {
            "source_paths": list(candidate.source_paths),
            "candidate": candidate.place_text,
            "nuts_codes": list(candidate.nuts_codes),
            "raw_warning": raw_warning,
            "geocoder_display_name": geocode.display_name if geocode else None,
        }
        await conn.execute(
            sa.text(
                """
                INSERT INTO act_locations (
                    id, act_id, nuts_code, municipality_code, municipality_name,
                    regional_unit_name, region_name, postal_code, place_text,
                    country_code, location_kind, granularity, extraction_method,
                    geocode_provider, confidence, evidence, enrichment_job_id,
                    enriched_at, source_record_id, geom
                ) VALUES (
                    :id, :act_id, :nuts_code, :municipality_code, :municipality_name,
                    :regional_unit_name, :region_name, :postal_code, :place_text,
                    :country_code, 'PERFORMANCE', :granularity, :extraction_method,
                    :geocode_provider, :confidence, CAST(:evidence AS JSONB), :job_id,
                    now(), :source_record_id,
                    CASE
                        WHEN CAST(:longitude AS DOUBLE PRECISION) IS NULL
                          OR CAST(:latitude AS DOUBLE PRECISION) IS NULL
                        THEN NULL
                        ELSE ST_SetSRID(
                            ST_MakePoint(
                                CAST(:longitude AS DOUBLE PRECISION),
                                CAST(:latitude AS DOUBLE PRECISION)
                            ),
                            4326
                        )
                    END
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "act_id": str(job.act_id),
                "nuts_code": candidate.nuts_codes[0] if candidate.nuts_codes else None,
                "municipality_code": None,
                "municipality_name": municipality,
                "regional_unit_name": regional_unit,
                "region_name": region,
                "postal_code": geocode.postal_code if geocode and geocode.postal_code else candidate.postal_code,
                "place_text": candidate.place_text,
                "country_code": geocode.country_code if geocode else "GR",
                "granularity": geocode.precision if geocode else candidate.granularity_hint,
                "extraction_method": candidate.extraction_method,
                "geocode_provider": geocode.provider if geocode else None,
                "confidence": geocode.confidence if geocode else candidate.confidence,
                "evidence": json.dumps(evidence, ensure_ascii=False),
                "job_id": str(job.id),
                "source_record_id": str(job.source_record_id),
                "longitude": longitude,
                "latitude": latitude,
            },
        )
        written += 1
    return written


async def process_job(
    conn: AsyncConnection,
    job: ClaimedJob,
    *,
    admin_units: Sequence[AdminUnit],
    gazetteer_places: Sequence[GazetteerPlace],
    gazetteer_alias_index: Mapping[str, Sequence[GazetteerPlace]] | None = None,
    remote: NominatimGeocoder | None,
) -> JobResult:
    row = (
        await conn.execute(
            sa.select(
                procurement_acts.c.source_record_id,
                procurement_acts.c.title,
                procurement_acts.c.is_current,
                source_records.c.payload_uri,
            )
            .select_from(procurement_acts.join(source_records, source_records.c.id == job.source_record_id))
            .where(procurement_acts.c.id == job.act_id)
        )
    ).first()
    if (
        row is None
        or row.source_record_id != job.source_record_id
        or not row.is_current
    ):
        return JobResult(job.id, "SUPERSEDED", 0, 0, 0)

    raw, raw_warning = _read_raw_payload(row.payload_uri)
    if row.title and "title" not in raw:
        raw["title"] = row.title
    if has_explicit_foreign_performance(raw):
        return JobResult(job.id, "OUTSIDE_GREECE", 0, 0, 0)
    texts = await _document_texts(conn, job.act_id)
    candidates = extract_location_candidates(raw, document_texts=texts, admin_units=admin_units)
    if not candidates:
        return JobResult(job.id, "NO_LOCATION", 0, 0, 0)
    candidates = await _attach_postal_nuts(conn, candidates)

    resolved = [
        await _resolve_candidate(
            conn,
            candidate,
            admin_units=admin_units,
            gazetteer_places=gazetteer_places,
            gazetteer_alias_index=gazetteer_alias_index,
            remote=remote,
        )
        for candidate in candidates
    ]
    written = await _write_locations(
        conn,
        job=job,
        candidates=candidates,
        resolved=resolved,
        raw_warning=raw_warning,
    )
    geocoded = sum(result is not None for result in resolved)
    status = "SUCCEEDED" if geocoded == len(candidates) else "PARTIAL"
    return JobResult(job.id, status, len(candidates), written, geocoded)


async def claim_jobs(
    conn: AsyncConnection,
    *,
    batch_size: int,
    worker_id: str,
) -> list[ClaimedJob]:
    await conn.execute(
        sa.text(
            """
            UPDATE geospatial_enrichment_jobs
            SET status = 'QUEUED', locked_at = NULL, locked_by = NULL
            WHERE status = 'RUNNING' AND locked_at < now() - interval '30 minutes'
            """
        )
    )
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH selected AS (
                    SELECT id
                    FROM geospatial_enrichment_jobs
                    WHERE status IN ('QUEUED', 'FAILED')
                      AND available_at <= now()
                      AND attempt_count < :max_attempts
                    ORDER BY available_at, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT :batch_size
                )
                UPDATE geospatial_enrichment_jobs job
                SET status = 'RUNNING',
                    locked_at = now(),
                    locked_by = :worker_id,
                    attempt_count = attempt_count + 1
                FROM selected
                WHERE job.id = selected.id
                RETURNING job.id, job.act_id, job.source_record_id, job.attempt_count
                """
            ),
            {"batch_size": batch_size, "worker_id": worker_id, "max_attempts": MAX_ATTEMPTS},
        )
    ).all()
    await conn.commit()
    return [ClaimedJob(row.id, row.act_id, row.source_record_id, row.attempt_count) for row in rows]


async def _finish_job(conn: AsyncConnection, result: JobResult) -> None:
    await conn.execute(
        geospatial_enrichment_jobs.update()
        .where(geospatial_enrichment_jobs.c.id == result.job_id)
        .values(
            status=result.status,
            result={
                "candidates_found": result.candidates_found,
                "locations_written": result.locations_written,
                "geocoded_locations": result.geocoded_locations,
            },
            last_error=None,
            locked_at=None,
            locked_by=None,
            finished_at=datetime.now(timezone.utc),
        )
    )
    await conn.commit()


async def _fail_job(conn: AsyncConnection, job: ClaimedJob, exc: Exception) -> None:
    delay_seconds = min(3600, 30 * (2 ** max(0, job.attempt_count - 1)))
    await conn.execute(
        sa.text(
            """
            UPDATE geospatial_enrichment_jobs
            SET status = 'FAILED',
                last_error = CAST(:error AS JSONB),
                available_at = now() + (:delay_seconds * interval '1 second'),
                locked_at = NULL,
                locked_by = NULL
            WHERE id = :job_id
            """
        ),
        {
            "job_id": str(job.id),
            "delay_seconds": delay_seconds,
            "error": json.dumps({"type": type(exc).__name__, "message": str(exc)}),
        },
    )
    await conn.commit()


async def run_pending_jobs(
    conn: AsyncConnection,
    *,
    batch_size: int = 50,
    worker_id: str | None = None,
    geocoder_config: GeocoderConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[JobResult]:
    worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
    jobs = await claim_jobs(conn, batch_size=batch_size, worker_id=worker_id)
    if not jobs:
        return []
    units = await load_admin_units(conn)
    gazetteer_places = await load_gazetteer_places(conn)
    gazetteer_alias_index = build_gazetteer_alias_index(gazetteer_places)
    remote = NominatimGeocoder(geocoder_config, client=http_client) if geocoder_config else None
    outcomes: list[JobResult] = []
    try:
        for job in jobs:
            try:
                result = await process_job(
                    conn,
                    job,
                    admin_units=units,
                    gazetteer_places=gazetteer_places,
                    gazetteer_alias_index=gazetteer_alias_index,
                    remote=remote,
                )
            except Exception as exc:  # noqa: BLE001 - job isolation/retry boundary
                await conn.rollback()
                await _fail_job(conn, job, exc)
                continue
            await _finish_job(conn, result)
            outcomes.append(result)
    finally:
        if remote:
            await remote.aclose()
    return outcomes


async def enqueue_existing_acts(
    conn: AsyncConnection,
    *,
    limit: int | None = None,
    requeue_partial: bool = False,
    requeue_all: bool = False,
) -> int:
    limit_sql = "LIMIT :limit" if limit else ""
    result = await conn.execute(
        sa.text(
            f"""
            INSERT INTO geospatial_enrichment_jobs (act_id, source_record_id)
            SELECT a.id, a.source_record_id
            FROM procurement_acts a
            WHERE a.is_current = TRUE
              AND a.act_type = ANY(CAST(:act_types AS TEXT[]))
            ORDER BY COALESCE(a.publication_date, a.submission_date, a.decision_date) DESC NULLS LAST
            {limit_sql}
            ON CONFLICT (act_id, source_record_id) DO UPDATE
            SET status = CASE
                    WHEN :requeue_all
                      OR (:requeue_partial AND geospatial_enrichment_jobs.status IN ('PARTIAL', 'NO_LOCATION'))
                    THEN 'QUEUED'
                    ELSE geospatial_enrichment_jobs.status
                END,
                available_at = CASE
                    WHEN :requeue_all
                      OR (:requeue_partial AND geospatial_enrichment_jobs.status IN ('PARTIAL', 'NO_LOCATION'))
                    THEN now()
                    ELSE geospatial_enrichment_jobs.available_at
                END,
                attempt_count = CASE
                    WHEN :requeue_all
                      OR (:requeue_partial AND geospatial_enrichment_jobs.status IN ('PARTIAL', 'NO_LOCATION'))
                    THEN 0
                    ELSE geospatial_enrichment_jobs.attempt_count
                END,
                locked_at = CASE
                    WHEN :requeue_all
                      OR (:requeue_partial AND geospatial_enrichment_jobs.status IN ('PARTIAL', 'NO_LOCATION'))
                    THEN NULL
                    ELSE geospatial_enrichment_jobs.locked_at
                END,
                locked_by = CASE
                    WHEN :requeue_all
                      OR (:requeue_partial AND geospatial_enrichment_jobs.status IN ('PARTIAL', 'NO_LOCATION'))
                    THEN NULL
                    ELSE geospatial_enrichment_jobs.locked_by
                END,
                last_error = CASE
                    WHEN :requeue_all
                      OR (:requeue_partial AND geospatial_enrichment_jobs.status IN ('PARTIAL', 'NO_LOCATION'))
                    THEN NULL
                    ELSE geospatial_enrichment_jobs.last_error
                END,
                finished_at = CASE
                    WHEN :requeue_all
                      OR (:requeue_partial AND geospatial_enrichment_jobs.status IN ('PARTIAL', 'NO_LOCATION'))
                    THEN NULL
                    ELSE geospatial_enrichment_jobs.finished_at
                END
            """
        ),
        {
            "act_types": list(ELIGIBLE_ACT_TYPES),
            "limit": limit,
            "requeue_partial": requeue_partial,
            "requeue_all": requeue_all,
        },
    )
    await conn.commit()
    return result.rowcount or 0

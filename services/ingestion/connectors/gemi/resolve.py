"""ΓΕΜΗ company enrichment — description.txt §18.1.

    νέος ΑΦΜ αναδόχου
      → έλεγχος τοπικού cache
      → έλεγχος checksum
      → enqueue ΓΕΜΗ lookup
      → αποθήκευση raw response
      → ενημέρωση company profile
      → δημιουργία temporal snapshot

Triggered per new/refreshed contractor entity from the ΚΗΜΔΗΣ pipeline
(`ActUpsertResult.contractor_entity_id`/`contractor_afm_normalized`), gated
by `cache.should_refresh()` so this never calls the ΓΕΜΗ API more often than
the policy allows. A negative result (company not found) is tracked with
its own dated `source_records` row — see module-level `_NOT_FOUND_RESOURCE`
— so it expires and gets rechecked per `cache.NEGATIVE_RESULT_REFRESH`
rather than being cached forever (§18.3).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import entity_company_snapshots, source_records
from packages.source_clients.raw_store import RawStore
from services.entity_resolution.resolve import find_or_create_entity_by_afm
from services.ingestion.connectors.khmdhs.afm import valid_greek_afm

from .cache import should_refresh
from .db_writer import ingest_company_record
from .provider import CompanyRegistryProvider

_NOT_FOUND_RESOURCE = "company_not_found"


@dataclass(frozen=True)
class SnapshotResolveResult:
    wrote_new_snapshot: bool
    # both populated only when wrote_new_snapshot is True and a *prior*
    # snapshot existed to compare against (a brand-new company has no
    # old_status to report a change from) — used by cli.py to fire
    # company.status_changed (§30.5) only when the status itself moved,
    # not on every unrelated field change (name, office, ...)
    old_status: str | None = None
    new_status: str | None = None


@dataclass(frozen=True)
class RegistryResolveResult:
    entity_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    wrote_new_snapshot: bool


async def _last_checked_at(conn: AsyncConnection, afm_normalized: str) -> datetime | None:
    row = (
        await conn.execute(
            select(func.max(source_records.c.fetched_at)).where(
                source_records.c.source_system == "GEMI",
                source_records.c.resource_type.in_(("company", _NOT_FOUND_RESOURCE)),
                source_records.c.source_native_id == afm_normalized,
            )
        )
    ).scalar()
    return row


async def _current_company_status(conn: AsyncConnection, entity_id: uuid.UUID) -> str | None:
    row = (
        await conn.execute(
            select(entity_company_snapshots.c.company_status).where(
                entity_company_snapshots.c.entity_id == entity_id,
                entity_company_snapshots.c.is_current.is_(True),
            )
        )
    ).first()
    return row.company_status if row is not None else None


async def _record_not_found(conn: AsyncConnection, *, afm_normalized: str, fetched_at: datetime) -> None:
    date_bucket = fetched_at.date().isoformat()
    content_sha256 = hashlib.sha256(f"{afm_normalized}:{date_bucket}".encode("utf-8")).hexdigest()

    already_recorded = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "GEMI",
                source_records.c.resource_type == _NOT_FOUND_RESOURCE,
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_recorded is not None:
        return

    await conn.execute(
        source_records.insert().values(
            id=uuid.uuid4(),
            source_system="GEMI",
            resource_type=_NOT_FOUND_RESOURCE,
            source_native_id=afm_normalized,
            content_sha256=content_sha256,
            payload_uri="",
            fetched_at=fetched_at,
            http_status=404,
            license_code="ODC-BY-1.0",
            parse_status="PARSED",
        )
    )


async def resolve_company_snapshot(
    conn: AsyncConnection,
    *,
    provider: CompanyRegistryProvider,
    raw_store: RawStore,
    afm_normalized: str,
    entity_id: uuid.UUID,
) -> SnapshotResolveResult:
    """`wrote_new_snapshot` is True if a new/changed snapshot was written,
    False if the refresh policy skipped the lookup or the company profile
    was unchanged. `old_status`/`new_status` let the caller (`cli.py`)
    decide whether to fire `company.status_changed` (§30.5) — set only
    when a snapshot was actually written."""
    now = datetime.now(timezone.utc)
    last_checked = await _last_checked_at(conn, afm_normalized)
    current_status = await _current_company_status(conn, entity_id)

    if not should_refresh(last_checked_at=last_checked, company_status=current_status, now=now):
        return SnapshotResolveResult(wrote_new_snapshot=False)

    result = await provider.find_by_vat("GR", afm_normalized)

    if result.company is None:
        await _record_not_found(conn, afm_normalized=afm_normalized, fetched_at=now)
        await conn.commit()
        return SnapshotResolveResult(wrote_new_snapshot=False)

    raw_body = result.raw_response.body if result.raw_response is not None else {}
    raw_ref = await raw_store.put(
        source="gemi",
        resource="company",
        partition_key=f"afm={afm_normalized}",
        payload=json.dumps(raw_body, sort_keys=True, ensure_ascii=False).encode("utf-8"),
    )

    ingest_result = await ingest_company_record(
        conn,
        entity_id=entity_id,
        normalized=result.company,
        raw_body=raw_body,
        payload_uri=raw_ref.payload_uri,
        content_sha256=raw_ref.content_sha256,
        http_status=result.raw_response.http_status if result.raw_response is not None else 200,
        fetched_at=now,
    )
    await conn.commit()

    wrote_new_snapshot = ingest_result.snapshot is not None and ingest_result.snapshot.wrote_new_snapshot
    if not wrote_new_snapshot:
        return SnapshotResolveResult(wrote_new_snapshot=False)
    return SnapshotResolveResult(
        wrote_new_snapshot=True, old_status=current_status, new_status=result.company.company_status
    )


async def resolve_company_by_gemi(
    conn: AsyncConnection,
    *,
    provider: CompanyRegistryProvider,
    raw_store: RawStore,
    gemi_number: str,
) -> RegistryResolveResult | None:
    """Resolve an official ΓΕΜΗ number to its exact AFM entity and persist it."""
    result = await provider.find_by_gemi(gemi_number)
    if result.company is None or result.raw_response is None:
        return None
    company = result.company
    entity_id = await find_or_create_entity_by_afm(
        conn,
        afm_raw=company.afm_raw,
        afm_normalized=company.afm_normalized,
        afm_checksum_valid=valid_greek_afm(company.afm_raw),
        name=company.official_name or company.trade_name,
        entity_type="COMPANY",
        source_record_id=None,
    )
    raw_body = result.raw_response.body
    raw_ref = await raw_store.put(
        source="gemi",
        resource="company",
        partition_key=f"gemi={gemi_number}",
        payload=json.dumps(raw_body, sort_keys=True, ensure_ascii=False).encode("utf-8"),
    )
    ingested = await ingest_company_record(
        conn,
        entity_id=entity_id,
        normalized=company,
        raw_body=raw_body,
        payload_uri=raw_ref.payload_uri,
        content_sha256=raw_ref.content_sha256,
        http_status=result.raw_response.http_status,
        fetched_at=datetime.now(timezone.utc),
    )
    await conn.commit()
    return RegistryResolveResult(
        entity_id=entity_id,
        snapshot_id=ingested.snapshot.snapshot_id if ingested.snapshot else None,
        wrote_new_snapshot=bool(
            ingested.snapshot and ingested.snapshot.wrote_new_snapshot
        ),
    )

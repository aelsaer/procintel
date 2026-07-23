"""Idempotent, temporal canonical writes for ΓΕΜΗ company data.

Two idempotency layers, same shape as every other connector plus one more:

1. `source_records` content-hash dedup (§16.2's pattern, reused here) — an
   unchanged payload writes nothing further.
2. Even on genuinely new content, a snapshot row is only written if the
   *company-relevant fields* actually differ from the current snapshot
   (§18.2: never overwrite — append a new temporal row instead, closing out
   the previous one). Re-fetching the same real-world state doesn't create
   snapshot churn; freshness of the *check* itself is tracked via
   `source_records.fetched_at`, not by re-writing unchanged snapshots.

`entity_identifiers(scheme='GEMI')` is attached to the same entity ΚΗΜΔΗΣ
already resolved by exact ΑΦΜ (§8 level 2) — ΓΕΜΗ never creates a new
entity, it enriches one that already exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import entity_company_snapshots, entity_identifiers, source_records

from .normalize import NormalizedCompany

_SNAPSHOT_COMPARISON_FIELDS = (
    "official_name",
    "trade_name",
    "legal_form",
    "legal_form_code",
    "company_status",
    "gemi_office",
    "municipality",
    "region",
)


@dataclass(frozen=True)
class SnapshotUpsertResult:
    snapshot_id: uuid.UUID
    wrote_new_snapshot: bool  # False if unchanged from the current snapshot


@dataclass(frozen=True)
class CompanyIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    snapshot: SnapshotUpsertResult | None  # None if deduped


async def _attach_gemi_identifier(
    conn: AsyncConnection, *, entity_id: uuid.UUID, gemi_number: str, source_record_id: uuid.UUID
) -> None:
    existing = (
        await conn.execute(
            select(entity_identifiers.c.id).where(
                entity_identifiers.c.scheme == "GEMI",
                entity_identifiers.c.value_normalized == gemi_number,
            )
        )
    ).first()
    if existing is None:
        await conn.execute(
            entity_identifiers.insert().values(
                id=uuid.uuid4(),
                entity_id=entity_id,
                scheme="GEMI",
                value_raw=gemi_number,
                value_normalized=gemi_number,
                country_code="GR",
                source_record_id=source_record_id,
            )
        )


async def upsert_company_snapshot(
    conn: AsyncConnection,
    *,
    entity_id: uuid.UUID,
    normalized: NormalizedCompany,
    source_record_id: uuid.UUID,
    observed_at: datetime,
) -> SnapshotUpsertResult:
    current = (
        await conn.execute(
            select(entity_company_snapshots).where(
                entity_company_snapshots.c.entity_id == entity_id,
                entity_company_snapshots.c.is_current.is_(True),
            )
        )
    ).first()

    new_values = dict(
        official_name=normalized.official_name,
        trade_name=normalized.trade_name,
        legal_form=normalized.legal_form,
        legal_form_code=normalized.legal_form_code,
        company_status=normalized.company_status,
        gemi_office=normalized.gemi_office,
        municipality=normalized.municipality,
        region=normalized.region,
    )

    if current is not None and all(
        getattr(current, field_name) == new_values[field_name] for field_name in _SNAPSHOT_COMPARISON_FIELDS
    ):
        return SnapshotUpsertResult(snapshot_id=current.id, wrote_new_snapshot=False)

    if current is not None:
        await conn.execute(
            entity_company_snapshots.update()
            .where(entity_company_snapshots.c.id == current.id)
            .values(is_current=False, valid_to=observed_at)
        )

    snapshot_id = uuid.uuid4()
    await conn.execute(
        entity_company_snapshots.insert().values(
            id=snapshot_id,
            entity_id=entity_id,
            source_record_id=source_record_id,
            gemi_number=normalized.gemi_number,
            vat_number=normalized.afm_normalized,
            gemi_registration_date=normalized.gemi_registration_date,
            kad_codes=normalized.kad_codes,
            observed_at=observed_at,
            valid_from=observed_at,
            is_current=True,
            **new_values,
        )
    )

    if normalized.gemi_number:
        await _attach_gemi_identifier(
            conn, entity_id=entity_id, gemi_number=normalized.gemi_number, source_record_id=source_record_id
        )

    return SnapshotUpsertResult(snapshot_id=snapshot_id, wrote_new_snapshot=True)


async def ingest_company_record(
    conn: AsyncConnection,
    *,
    entity_id: uuid.UUID,
    normalized: NormalizedCompany,
    raw_body: dict[str, Any],
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> CompanyIngestResult:
    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "GEMI",
                source_records.c.resource_type == "company",
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_seen is not None:
        return CompanyIngestResult(source_record_id=None, snapshot=None)

    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="GEMI",
            resource_type="company",
            source_native_id=normalized.afm_normalized,
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code="ODC-BY-1.0",
            parse_status="PARSED",
        )
    )
    snapshot = await upsert_company_snapshot(
        conn,
        entity_id=entity_id,
        normalized=normalized,
        source_record_id=source_record_id,
        observed_at=fetched_at,
    )
    return CompanyIngestResult(source_record_id=source_record_id, snapshot=snapshot)

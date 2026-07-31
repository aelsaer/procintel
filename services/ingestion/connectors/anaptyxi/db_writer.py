"""Idempotent canonical writes for ΑΝΑΠΤΥΞΗ funding projects.

Same dedup-then-upsert shape as every other connector, with one difference:
`funding_projects` has no DB-level unique constraint on
`(mis_ops_code, program_period)` (only a regular index, per
db/migrations/05_funding_and_external_sources.sql — ΑΝΑΠΤΥΞΗ's real
identifier situation is looser than ΚΗΜΔΗΣ's ΑΔΑΜ or Διαύγεια's ΑΔΑ), so
upsert-in-place is done in application code: find-by-(mis_ops_code,
program_period), update if found, insert if not.

Beneficiary entity resolution reuses `services/entity_resolution/resolve.py`
— same exact-ΑΦΜ rule as every other connector, no new identity logic here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    funding_geographic_allocations,
    funding_payment_snapshots,
    funding_project_bodies,
    funding_projects,
    funding_subprojects,
    source_records,
)
from services.entity_resolution.resolve import find_or_create_entity_by_afm
from services.ingestion.connectors.khmdhs.afm import valid_greek_afm

from .normalize import NormalizedFundingProject, normalize_project_record


@dataclass(frozen=True)
class FundingProjectUpsertResult:
    funding_project_id: uuid.UUID
    is_new: bool


@dataclass(frozen=True)
class FundingIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    project: FundingProjectUpsertResult | None  # None if deduped


async def upsert_funding_project(
    conn: AsyncConnection,
    *,
    normalized: NormalizedFundingProject,
    program_period: str,
    source_record_id: uuid.UUID,
) -> FundingProjectUpsertResult:
    existing = (
        await conn.execute(
            select(funding_projects.c.id).where(
                funding_projects.c.mis_ops_code == normalized.mis_ops_code,
                funding_projects.c.program_period == program_period,
            )
        )
    ).first()

    beneficiary_id: uuid.UUID | None = None
    if normalized.beneficiary_afm:
        afm_digits = "".join(ch for ch in normalized.beneficiary_afm if ch.isdigit())
        beneficiary_id = await find_or_create_entity_by_afm(
            conn,
            afm_raw=normalized.beneficiary_afm,
            afm_normalized=afm_digits,
            afm_checksum_valid=valid_greek_afm(normalized.beneficiary_afm),
            name=normalized.beneficiary_name,
            entity_type="PUBLIC_ORGANIZATION",
            source_record_id=source_record_id,
        )

    values: dict[str, Any] = dict(
        program_code=normalized.program_code,
        program_period=program_period,
        title=normalized.title,
        beneficiary_id=beneficiary_id,
        beneficiary_name_raw=normalized.beneficiary_name,
        budget=normalized.budget,
        total_budget=normalized.total_budget,
        contracted_amount=normalized.contracted_amount,
        paid_amount=normalized.paid_amount,
        completion=normalized.completion,
        absorption=normalized.absorption,
        currency=normalized.currency,
        start_date=normalized.start_date,
        end_date=normalized.end_date,
        status=normalized.status,
        description=normalized.description,
        program_title=normalized.program_title,
        is_state_aid=normalized.is_state_aid,
        is_major=normalized.is_major,
        status_report=normalized.status_report,
        status_report_date=normalized.status_report_date,
        funds=normalized.funds,
        spatial=normalized.spatial,
        thematic=normalized.thematic,
        map_kml=normalized.map_kml,
        details={
            **normalized.details,
            "files": normalized.files,
        },
        observed_at=datetime.now(timezone.utc),
        source_record_id=source_record_id,
    )

    if existing is not None:
        await conn.execute(funding_projects.update().where(funding_projects.c.id == existing.id).values(**values))
        return FundingProjectUpsertResult(funding_project_id=existing.id, is_new=False)

    project_id = uuid.uuid4()
    await conn.execute(
        funding_projects.insert().values(id=project_id, mis_ops_code=normalized.mis_ops_code, **values)
    )
    return FundingProjectUpsertResult(funding_project_id=project_id, is_new=True)


async def _replace_project_details(
    conn: AsyncConnection,
    *,
    project_id: uuid.UUID,
    normalized: NormalizedFundingProject,
    source_record_id: uuid.UUID,
    observed_at: datetime,
) -> None:
    """Replace source-owned hierarchy rows for one changed project snapshot."""
    await conn.execute(
        funding_payment_snapshots.delete().where(
            funding_payment_snapshots.c.funding_project_id == project_id
        )
    )
    await conn.execute(
        funding_project_bodies.delete().where(
            funding_project_bodies.c.funding_project_id == project_id
        )
    )
    await conn.execute(
        funding_geographic_allocations.delete().where(
            funding_geographic_allocations.c.funding_project_id == project_id
        )
    )
    await conn.execute(
        funding_subprojects.delete().where(
            funding_subprojects.c.funding_project_id == project_id
        )
    )

    if normalized.paid_amount is not None:
        await conn.execute(
            funding_payment_snapshots.insert().values(
                id=uuid.uuid4(),
                funding_project_id=project_id,
                amount=normalized.paid_amount,
                payment_scope="PROJECT_TOTAL",
                reference_date=normalized.status_report_date.date()
                if normalized.status_report_date
                else None,
                source_record_id=source_record_id,
                observed_at=observed_at,
            )
        )

    subproject_ids: dict[int, uuid.UUID] = {}
    for subproject in normalized.subprojects:
        subproject_id = uuid.uuid4()
        subproject_ids[subproject.subproject_index] = subproject_id
        await conn.execute(
            funding_subprojects.insert().values(
                id=subproject_id,
                funding_project_id=project_id,
                subproject_index=subproject.subproject_index,
                title=subproject.title,
                implementors=subproject.implementors,
                budget=subproject.budget,
                paid_amount=subproject.paid_amount,
                completion=subproject.completion,
                start_date=subproject.start_date,
                end_date=subproject.end_date,
                subproject_type=subproject.subproject_type,
                is_grant=subproject.is_grant,
                estimated_status=subproject.estimated_status,
                actual_status=subproject.actual_status,
                details={**subproject.details, "files": subproject.files},
                source_record_id=source_record_id,
                observed_at=observed_at,
            )
        )
        if subproject.paid_amount is not None:
            await conn.execute(
                funding_payment_snapshots.insert().values(
                    id=uuid.uuid4(),
                    funding_project_id=project_id,
                    funding_subproject_id=subproject_id,
                    amount=subproject.paid_amount,
                    payment_scope="SUBPROJECT_TOTAL",
                    reference_date=subproject.end_date,
                    source_record_id=source_record_id,
                    observed_at=observed_at,
                )
            )
        for body in subproject.bodies:
            if not body.get("name"):
                continue
            await conn.execute(
                funding_project_bodies.insert().values(
                    id=uuid.uuid4(),
                    funding_project_id=project_id,
                    funding_subproject_id=subproject_id,
                    body_category=body.get("bodyCategory"),
                    name=body["name"],
                    representative=body.get("representative"),
                    address=body.get("address"),
                    telephone=body.get("telephone"),
                    email=body.get("email"),
                    fax=body.get("fax"),
                    source_record_id=source_record_id,
                    observed_at=observed_at,
                )
            )

    for body in normalized.bodies:
        if not body.get("name"):
            continue
        await conn.execute(
            funding_project_bodies.insert().values(
                id=uuid.uuid4(),
                funding_project_id=project_id,
                body_category=body.get("bodyCategory"),
                name=body["name"],
                representative=body.get("representative"),
                address=body.get("address"),
                telephone=body.get("telephone"),
                email=body.get("email"),
                fax=body.get("fax"),
                source_record_id=source_record_id,
                observed_at=observed_at,
            )
        )

    for allocation in normalized.geographic_allocations:
        await conn.execute(
            funding_geographic_allocations.insert().values(
                id=uuid.uuid4(),
                funding_project_id=project_id,
                region=allocation.get("region"),
                prefecture=allocation.get("prefecture"),
                municipality=allocation.get("municipality"),
                region_code=str(allocation.get("regionCode") or "") or None,
                prefecture_code=str(allocation.get("prefectureCode") or "") or None,
                municipality_code=str(allocation.get("municipalityCode") or "") or None,
                amount=allocation.get("amount"),
                percentage=allocation.get("percentage"),
                source_record_id=source_record_id,
                observed_at=observed_at,
            )
        )


async def ingest_project_record(
    conn: AsyncConnection,
    *,
    mis_code: str,
    program_period: str,
    raw_body: dict[str, Any],
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> FundingIngestResult:
    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "ANAPTYXI",
                source_records.c.resource_type == program_period,
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_seen is not None:
        return FundingIngestResult(source_record_id=None, project=None)

    normalized = normalize_project_record(raw_body, mis_code=mis_code)
    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="ANAPTYXI",
            resource_type=program_period,
            source_native_id=mis_code,
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code="UNCONFIRMED",
            parse_status="PARSED",
        )
    )
    project = await upsert_funding_project(
        conn, normalized=normalized, program_period=program_period, source_record_id=source_record_id
    )
    await _replace_project_details(
        conn,
        project_id=project.funding_project_id,
        normalized=normalized,
        source_record_id=source_record_id,
        observed_at=fetched_at,
    )
    return FundingIngestResult(source_record_id=source_record_id, project=project)

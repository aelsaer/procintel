"""Canonical writes and local backfill helpers for process participation."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_parties,
    documents,
    process_participations,
    procurement_acts,
)
from services.documents.entities import ExtractedProcurementParticipant
from services.entity_resolution.resolve import find_or_create_entity_by_afm


@dataclass(frozen=True)
class ParticipationBackfillResult:
    winners_seen: int = 0
    document_participants_seen: int = 0
    rows_inserted: int = 0


def _evidence_key(
    *,
    process_id: uuid.UUID,
    role: str,
    entity_id: uuid.UUID | None,
    participant_name: str | None,
    participant_afm: str | None,
    source_record_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
    source_page: int | None,
) -> str:
    identity = str(entity_id) if entity_id else f"{participant_afm or ''}|{(participant_name or '').strip().upper()}"
    raw = "|".join(
        (
            str(process_id),
            role,
            identity,
            str(source_record_id or ""),
            str(document_id or ""),
            str(source_page or ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def record_participation(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID,
    role: str,
    evidence_type: str,
    confidence: float,
    entity_id: uuid.UUID | None = None,
    participant_name: str | None = None,
    participant_afm: str | None = None,
    act_id: uuid.UUID | None = None,
    source_record_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    source_page: int | None = None,
    evidence: dict[str, Any] | None = None,
    observed_at: datetime | None = None,
) -> bool:
    key = _evidence_key(
        process_id=process_id,
        role=role,
        entity_id=entity_id,
        participant_name=participant_name,
        participant_afm=participant_afm,
        source_record_id=source_record_id,
        document_id=document_id,
        source_page=source_page,
    )
    statement = (
        insert(process_participations)
        .values(
            id=uuid.uuid4(),
            process_id=process_id,
            act_id=act_id,
            entity_id=entity_id,
            participant_name_raw=participant_name,
            participant_afm_raw=participant_afm,
            participation_role=role,
            evidence_type=evidence_type,
            confidence=confidence,
            source_record_id=source_record_id,
            document_id=document_id,
            source_page=source_page,
            evidence=evidence or {},
            evidence_key=key,
            observed_at=observed_at or datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=[process_participations.c.evidence_key])
        .returning(process_participations.c.id)
    )
    return (await conn.execute(statement)).first() is not None


async def record_document_participant(
    conn: AsyncConnection,
    *,
    act_id: uuid.UUID,
    document_id: uuid.UUID,
    source_record_id: uuid.UUID,
    source_page: int,
    participant: ExtractedProcurementParticipant,
    confidence_scale: float = 1.0,
) -> bool:
    process_row = (
        await conn.execute(
            sa.select(procurement_acts.c.process_id).where(procurement_acts.c.id == act_id)
        )
    ).first()
    if process_row is None or process_row.process_id is None:
        return False

    entity_id = await find_or_create_entity_by_afm(
        conn,
        afm_raw=participant.afm,
        afm_normalized=participant.afm,
        afm_checksum_valid=participant.checksum_valid,
        name=participant.name,
        entity_type="COMPANY",
        source_record_id=source_record_id,
    )
    return await record_participation(
        conn,
        process_id=process_row.process_id,
        act_id=act_id,
        entity_id=entity_id,
        participant_name=participant.name,
        participant_afm=participant.afm,
        role=participant.role,
        evidence_type="DOCUMENT_EXTRACTED",
        confidence=round(participant.confidence * confidence_scale, 4),
        source_record_id=source_record_id,
        document_id=document_id,
        source_page=source_page,
        evidence={"role_label": participant.role_label, "extractor": "PROCUREMENT_ROLE_AFM_REGEX"},
    )


async def backfill_winner_participations(conn: AsyncConnection) -> tuple[int, int]:
    rows = (
        await conn.execute(
            sa.select(
                procurement_acts.c.process_id,
                procurement_acts.c.id.label("act_id"),
                act_parties.c.entity_id,
                act_parties.c.party_role,
                act_parties.c.source_record_id,
            )
            .select_from(act_parties.join(procurement_acts, procurement_acts.c.id == act_parties.c.act_id))
            .where(
                procurement_acts.c.process_id.is_not(None),
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
            )
        )
    ).all()
    inserted = 0
    for row in rows:
        inserted += int(
            await record_participation(
                conn,
                process_id=row.process_id,
                act_id=row.act_id,
                entity_id=row.entity_id,
                role="WINNER",
                evidence_type="OFFICIAL_SOURCE",
                confidence=1.0,
                source_record_id=row.source_record_id,
                evidence={"source_party_role": row.party_role},
            )
        )
    return len(rows), inserted


async def load_document_pages_for_participation_backfill(conn: AsyncConnection):
    return (
        await conn.execute(
            sa.text(
                """
                SELECT d.id AS document_id, d.act_id, d.source_record_id,
                       dp.page_number, dp.text,
                       CASE WHEN dp.extraction_method = 'OCR'
                            THEN COALESCE(dp.ocr_mean_confidence / 100.0, 0.5)
                            ELSE 1.0 END AS confidence_scale
                FROM documents d
                JOIN document_pages dp ON dp.document_id = d.id
                JOIN procurement_acts a ON a.id = d.act_id
                WHERE d.act_id IS NOT NULL
                  AND d.source_record_id IS NOT NULL
                  AND a.process_id IS NOT NULL
                  AND NULLIF(BTRIM(dp.text), '') IS NOT NULL
                ORDER BY d.id, dp.page_number
                """
            )
        )
    ).all()


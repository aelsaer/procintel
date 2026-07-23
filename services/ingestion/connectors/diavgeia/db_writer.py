"""Idempotent canonical writes for Διαύγεια decisions.

Mirrors `services/ingestion/connectors/khmdhs/db_writer.py`'s shape: dedup
on content_sha256 in `source_records`, upsert-in-place on the ΑΔΑ
identifier. The issuing authority/organizational unit still get no
`act_parties` (see `normalize.py`'s module docstring — no reliable
identifier). Signers are the one exception: §6.3 explicitly permits
name-only `PERSON` entities for signers, so each decision's signer names
become (or are matched to) `PERSON` entities linked via
`act_parties(party_role='SIGNER_PERSON')` — replaced wholesale on every
upsert (delete-then-reinsert), same pattern `act_cpv_codes`/`act_locations`
already use elsewhere for a "current signer list" that's re-derived fresh
from each fetch, not accumulated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import act_identifiers, act_parties, entities, procurement_acts, source_records

from .normalize import NormalizedDecision, normalize_ada, normalize_decision_record


@dataclass(frozen=True)
class DecisionUpsertResult:
    act_id: uuid.UUID
    is_new: bool


@dataclass(frozen=True)
class DecisionIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    act_id: uuid.UUID | None  # populated even on dedup, via ADA lookup — see ingest_decision_record
    document_url: str | None = None  # only populated on a fresh (non-deduped) ingestion — see ingest_decision_record


async def _find_or_create_person_entity(conn: AsyncConnection, *, name: str) -> uuid.UUID:
    """No identifier exists for a signer (§6.3 permits name-only PERSON
    entities specifically because of this) — dedup is on normalized name
    alone, a weaker identity guarantee than the ΑΦΜ-based rule everywhere
    else in this codebase, and a known limitation: two different people
    who happen to share a name become one PERSON entity here."""
    normalized_name = name.strip().upper()
    existing = (
        await conn.execute(
            select(entities.c.id).where(
                entities.c.entity_type == "PERSON", entities.c.normalized_name == normalized_name
            )
        )
    ).first()
    if existing is not None:
        return existing.id

    person_id = uuid.uuid4()
    await conn.execute(
        entities.insert().values(
            id=person_id,
            entity_type="PERSON",
            canonical_name=name.strip(),
            normalized_name=normalized_name,
        )
    )
    return person_id


async def _replace_signers(
    conn: AsyncConnection, *, act_id: uuid.UUID, signer_names: list[str], source_record_id: uuid.UUID
) -> None:
    await conn.execute(
        act_parties.delete().where(act_parties.c.act_id == act_id, act_parties.c.party_role == "SIGNER_PERSON")
    )
    for name in signer_names:
        person_id = await _find_or_create_person_entity(conn, name=name)
        await conn.execute(
            act_parties.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                entity_id=person_id,
                party_role="SIGNER_PERSON",
                source_record_id=source_record_id,
            )
        )


async def upsert_decision_act(
    conn: AsyncConnection,
    *,
    normalized: NormalizedDecision,
    source_record_id: uuid.UUID,
) -> DecisionUpsertResult:
    existing = await conn.execute(
        select(act_identifiers.c.act_id).where(
            act_identifiers.c.scheme == "ADA",
            act_identifiers.c.value_normalized == normalized.ada_normalized,
        )
    )
    row = existing.first()
    is_new = row is None
    act_id = row.act_id if row is not None else uuid.uuid4()

    act_values: dict[str, Any] = dict(
        act_type="DIAVGEIA_DECISION",
        title=normalized.subject,
        normalized_title=normalized.subject.upper() if normalized.subject else None,
        decision_date=normalized.decision_date,
        status=normalized.decision_type,
        source_record_id=source_record_id,
        updated_at=datetime.utcnow(),
    )

    if is_new:
        await conn.execute(procurement_acts.insert().values(id=act_id, **act_values))
        await conn.execute(
            act_identifiers.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                scheme="ADA",
                value_raw=normalized.ada_raw,
                value_normalized=normalized.ada_normalized,
                source_record_id=source_record_id,
            )
        )
    else:
        await conn.execute(
            procurement_acts.update().where(procurement_acts.c.id == act_id).values(**act_values)
        )

    # unconditional, like act_cpv_codes/act_locations elsewhere — clears
    # stale signer rows if a re-fetch no longer lists any
    await _replace_signers(
        conn, act_id=act_id, signer_names=normalized.signer_names, source_record_id=source_record_id
    )

    return DecisionUpsertResult(act_id=act_id, is_new=is_new)


async def ingest_decision_record(
    conn: AsyncConnection,
    *,
    ada: str,
    raw_body: dict[str, Any],
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> DecisionIngestResult:
    """`source_record_id` is None if this exact payload was already
    ingested (content_sha256 dedup); `act_id` is still populated in that
    case (looked up via the ΑΔΑ identifier) so callers can always link to
    it, not just on the first ingestion. `document_url` is only populated
    on a fresh ingestion — a caller wanting the decision's own PDF
    processed (`services/documents/pipeline.py::process_document()`) only
    needs to act on it once, the same "already fully processed" logic
    every dedup path in this codebase already follows."""
    ada_normalized = normalize_ada(ada)

    already_seen = await conn.execute(
        select(source_records.c.id).where(
            source_records.c.source_system == "DIAVGEIA",
            source_records.c.resource_type == "decision",
            source_records.c.content_sha256 == content_sha256,
        )
    )
    if already_seen.first() is not None:
        existing_act = (
            await conn.execute(
                select(act_identifiers.c.act_id).where(
                    act_identifiers.c.scheme == "ADA",
                    act_identifiers.c.value_normalized == ada_normalized,
                )
            )
        ).first()
        return DecisionIngestResult(
            source_record_id=None, act_id=existing_act.act_id if existing_act is not None else None
        )

    normalized = normalize_decision_record(raw_body, ada=ada)
    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="DIAVGEIA",
            resource_type="decision",
            source_native_id=normalized.ada_normalized,
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code="CC-BY-4.0",  # TODO(confirm): Διαύγεια's exact license terms
            parse_status="PARSED",
        )
    )
    upsert_result = await upsert_decision_act(conn, normalized=normalized, source_record_id=source_record_id)
    return DecisionIngestResult(
        source_record_id=source_record_id, act_id=upsert_result.act_id, document_url=normalized.document_url
    )

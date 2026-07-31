"""Shared entity resolution — description.txt §8, §25.

Relocated out of `connectors/khmdhs/db_writer.py` (where it started as a
connector-local helper) so ΓΕΜΗ and every later connector reuse the exact
same identity rule instead of re-implementing it. Deliberately takes plain
ΑΦΜ fields rather than any one connector's normalized-record type, so this
module has no dependency on `services/ingestion/connectors/*`.

Exact-ΑΦΜ resolution remains automatic. Records without a shared exact
identifier are handled by ``candidates.py`` using explainable multi-field
blocking/scoring and the persisted review/merge workflow.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import entities, entity_identifiers


async def find_or_create_entity_by_afm(
    conn: AsyncConnection,
    *,
    afm_raw: str,
    afm_normalized: str,
    afm_checksum_valid: bool,
    name: str | None,
    entity_type: str,
    source_record_id: uuid.UUID | None,
) -> uuid.UUID:
    """Exact-ΑΦΜ identity resolution. Never creates a second entity for an
    ΑΦΜ that already has a current identifier row — the DB's unique index
    on (scheme, country, value) WHERE is_current AND confidence=1 backs
    this up at the constraint level too. A failed checksum still gets an
    entity (§7.2: invalid checksum means `identifier_valid=false` /
    `match_eligibility=restricted`, not record rejection)."""
    existing = await conn.execute(
        select(entity_identifiers.c.entity_id).where(
            entity_identifiers.c.scheme == "AFM",
            entity_identifiers.c.value_normalized == afm_normalized,
            entity_identifiers.c.is_current.is_(True),
        )
    )
    row = existing.first()
    if row is not None:
        return row.entity_id

    entity_id = uuid.uuid4()
    display_name = name or afm_normalized
    await conn.execute(
        entities.insert().values(
            id=entity_id,
            entity_type=entity_type,
            canonical_name=display_name,
            normalized_name=display_name.upper(),
            country_code="GR",
        )
    )
    await conn.execute(
        entity_identifiers.insert().values(
            id=uuid.uuid4(),
            entity_id=entity_id,
            scheme="AFM",
            value_raw=afm_raw,
            value_normalized=afm_normalized,
            country_code="GR",
            source_record_id=source_record_id,
            confidence=1,  # source-provided value, not a fuzzy match; identifier_valid carries the checksum result
            identifier_valid=afm_checksum_valid,
            match_eligibility="ELIGIBLE" if afm_checksum_valid else "RESTRICTED",
        )
    )
    return entity_id

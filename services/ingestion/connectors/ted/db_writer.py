"""Idempotent canonical writes for TED notices.

Same dedup-then-upsert shape as every other connector. Buyer/supplier entity
resolution is TED-specific rather than reusing
`services/entity_resolution/resolve.py`: that module's exact-ΑΦΜ rule
(§8 level 2) is Greek-ΑΦΜ-checksum-specific, but a TED party can be from any
EU country. For a Greek party (`country_code == 'GR'`) the same shared
resolver is used (still the single source of truth for "how do we resolve a
Greek ΑΦΜ"); for a non-Greek party, a separate lookup-or-create keyed on
`entity_identifiers(scheme='EU_VAT', country_code, value_normalized)` is
used instead — no checksum validation is attempted (the Greek ΑΦΜ algorithm
doesn't apply), and the identifier's actual validity is a job for VIES
(`services/ingestion/connectors/vies`), recorded separately in
`entity_vies_checks`, not conflated with `identifier_valid` here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_cpv_codes,
    act_identifiers,
    act_locations,
    act_parties,
    entities,
    entity_identifiers,
    procurement_acts,
    source_records,
    ted_notice_details,
)
from services.entity_resolution.resolve import find_or_create_entity_by_afm
from services.ingestion.connectors.khmdhs.afm import valid_greek_afm

from .normalize import NormalizedTedNotice, NormalizedTedParty, normalize_ted_notice


@dataclass(frozen=True)
class TedNoticeUpsertResult:
    act_id: uuid.UUID
    is_new: bool
    supplier_entity_id: uuid.UUID | None
    supplier_country_code: str | None
    supplier_vat: str | None
    buyer_entity_id: uuid.UUID | None
    cpv_codes: list[str]
    publication_date: date | None
    title: str | None = None  # trigger data for resolve.py's Level 4 (title+amount+date) linkage
    amount: Decimal | None = None  # awarded_value if known, else estimated_value


@dataclass(frozen=True)
class TedIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    notice: TedNoticeUpsertResult | None  # None if deduped


async def load_existing_notice_context(
    conn: AsyncConnection,
    *,
    ted_notice_id: str,
    raw_body: dict[str, Any],
) -> TedIngestResult:
    """Rebuilds process-matching/VIES context for an unchanged TED notice."""
    normalized = normalize_ted_notice(raw_body, ted_notice_id=ted_notice_id)
    act_id = (
        await conn.execute(
            select(act_identifiers.c.act_id).where(
                act_identifiers.c.scheme == "TED_NOTICE_ID",
                act_identifiers.c.value_normalized == ted_notice_id,
            )
        )
    ).scalar()
    if act_id is None:
        return TedIngestResult(source_record_id=None, notice=None)

    party_rows = (
        await conn.execute(
            select(act_parties.c.party_role, act_parties.c.entity_id).where(act_parties.c.act_id == act_id)
        )
    ).all()
    party_entities = {row.party_role: row.entity_id for row in party_rows}
    return TedIngestResult(
        source_record_id=None,
        notice=TedNoticeUpsertResult(
            act_id=act_id,
            is_new=False,
            supplier_entity_id=party_entities.get("SUPPLIER"),
            supplier_country_code=normalized.supplier.country_code if normalized.supplier else None,
            supplier_vat=normalized.supplier.vat if normalized.supplier else None,
            buyer_entity_id=party_entities.get("BUYER"),
            cpv_codes=normalized.cpv_codes,
            publication_date=normalized.publication_date,
            title=normalized.title,
            amount=normalized.awarded_value or normalized.estimated_value,
        ),
    )


async def _resolve_eu_vat_entity(
    conn: AsyncConnection, *, party: NormalizedTedParty, entity_type: str, source_record_id: uuid.UUID
) -> uuid.UUID | None:
    if not party.vat or not party.country_code:
        return None

    vat_digits = "".join(ch for ch in party.vat if ch.isalnum()).upper()

    existing = (
        await conn.execute(
            select(entity_identifiers.c.entity_id).where(
                entity_identifiers.c.scheme == "EU_VAT",
                entity_identifiers.c.country_code == party.country_code,
                entity_identifiers.c.value_normalized == vat_digits,
                entity_identifiers.c.is_current.is_(True),
            )
        )
    ).first()
    if existing is not None:
        return existing.entity_id

    entity_id = uuid.uuid4()
    display_name = party.name or vat_digits
    await conn.execute(
        entities.insert().values(
            id=entity_id,
            entity_type=entity_type,
            canonical_name=display_name,
            normalized_name=display_name.upper(),
            country_code=party.country_code,
        )
    )
    await conn.execute(
        entity_identifiers.insert().values(
            id=uuid.uuid4(),
            entity_id=entity_id,
            scheme="EU_VAT",
            value_raw=party.vat,
            value_normalized=vat_digits,
            country_code=party.country_code,
            source_record_id=source_record_id,
            confidence=1,
            identifier_valid=True,  # not checksum-validated here — VIES does that separately
            match_eligibility="ELIGIBLE",
        )
    )
    return entity_id


async def _resolve_party_entity(
    conn: AsyncConnection, *, party: NormalizedTedParty | None, entity_type: str, source_record_id: uuid.UUID
) -> uuid.UUID | None:
    if party is None or not party.vat:
        return None
    if party.country_code == "GR":
        vat_digits = "".join(ch for ch in party.vat if ch.isdigit())
        return await find_or_create_entity_by_afm(
            conn,
            afm_raw=party.vat,
            afm_normalized=vat_digits,
            afm_checksum_valid=valid_greek_afm(party.vat),
            name=party.name,
            entity_type=entity_type,
            source_record_id=source_record_id,
        )
    return await _resolve_eu_vat_entity(
        conn, party=party, entity_type=entity_type, source_record_id=source_record_id
    )


async def upsert_ted_notice(
    conn: AsyncConnection,
    *,
    normalized: NormalizedTedNotice,
    raw_format: str,
    source_record_id: uuid.UUID,
) -> TedNoticeUpsertResult:
    existing = (
        await conn.execute(
            select(act_identifiers.c.act_id).where(
                act_identifiers.c.scheme == "TED_NOTICE_ID",
                act_identifiers.c.value_normalized == normalized.ted_notice_id,
            )
        )
    ).first()
    is_new = existing is None
    act_id = existing.act_id if existing is not None else uuid.uuid4()

    act_values: dict[str, Any] = dict(
        act_type="TED_NOTICE",
        title=normalized.title,
        normalized_title=normalized.title.upper() if normalized.title else None,
        publication_date=normalized.publication_date,
        amount_net=normalized.estimated_value,
        amount_gross=normalized.awarded_value,
        procedure_type=normalized.procedure_type,
        source_record_id=source_record_id,
        updated_at=datetime.utcnow(),
    )

    if is_new:
        await conn.execute(procurement_acts.insert().values(id=act_id, **act_values))
        await conn.execute(
            act_identifiers.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                scheme="TED_NOTICE_ID",
                value_raw=normalized.ted_notice_id,
                value_normalized=normalized.ted_notice_id,
                source_record_id=source_record_id,
            )
        )
    else:
        await conn.execute(procurement_acts.update().where(procurement_acts.c.id == act_id).values(**act_values))

    await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id == act_id))
    for index, cpv in enumerate(normalized.cpv_codes):
        await conn.execute(
            act_cpv_codes.insert().values(
                act_id=act_id, cpv_code=cpv, is_primary=(index == 0), source_record_id=source_record_id
            )
        )

    await conn.execute(
        act_locations.delete().where(
            act_locations.c.act_id == act_id,
            act_locations.c.enrichment_job_id.is_(None),
        )
    )
    for nuts_code in normalized.nuts_codes:
        await conn.execute(
            act_locations.insert().values(
                id=uuid.uuid4(), act_id=act_id, nuts_code=nuts_code, source_record_id=source_record_id
            )
        )

    buyer_entity_id = await _resolve_party_entity(
        conn, party=normalized.buyer, entity_type="PUBLIC_ORGANIZATION", source_record_id=source_record_id
    )
    supplier_entity_id = await _resolve_party_entity(
        conn, party=normalized.supplier, entity_type="COMPANY", source_record_id=source_record_id
    )

    await conn.execute(act_parties.delete().where(act_parties.c.act_id == act_id))
    if buyer_entity_id is not None:
        await conn.execute(
            act_parties.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                entity_id=buyer_entity_id,
                party_role="BUYER",
                source_record_id=source_record_id,
            )
        )
    if supplier_entity_id is not None:
        await conn.execute(
            act_parties.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                entity_id=supplier_entity_id,
                party_role="SUPPLIER",
                amount=normalized.awarded_value,
                source_record_id=source_record_id,
            )
        )

    existing_details = (
        await conn.execute(select(ted_notice_details.c.id).where(ted_notice_details.c.act_id == act_id))
    ).first()
    detail_values = dict(
        ted_notice_id=normalized.ted_notice_id,
        publication_number=normalized.publication_number,
        raw_format=raw_format,
        notice_type=normalized.notice_type,
        eforms_version=normalized.eforms_version,
        parser_version=normalized.parser_version,
        parse_confidence=normalized.parse_confidence,
        buyer_raw=normalized.buyer.model_dump() if normalized.buyer else None,
        supplier_raw=normalized.supplier.model_dump() if normalized.supplier else None,
        country_code=normalized.country_code,
        nuts_codes=normalized.nuts_codes,
        related_notice_ids=normalized.related_notice_ids,
        source_record_id=source_record_id,
    )
    if existing_details is not None:
        await conn.execute(
            ted_notice_details.update()
            .where(ted_notice_details.c.id == existing_details.id)
            .values(**detail_values)
        )
    else:
        await conn.execute(ted_notice_details.insert().values(id=uuid.uuid4(), act_id=act_id, **detail_values))

    return TedNoticeUpsertResult(
        act_id=act_id,
        is_new=is_new,
        supplier_entity_id=supplier_entity_id,
        supplier_country_code=normalized.supplier.country_code if normalized.supplier else None,
        supplier_vat=normalized.supplier.vat if normalized.supplier else None,
        buyer_entity_id=buyer_entity_id,
        cpv_codes=normalized.cpv_codes,
        publication_date=normalized.publication_date,
        title=normalized.title,
        amount=normalized.awarded_value or normalized.estimated_value,
    )


async def ingest_notice_record(
    conn: AsyncConnection,
    *,
    ted_notice_id: str,
    raw_body: dict[str, Any],
    raw_format: str,
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> TedIngestResult:
    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "TED",
                source_records.c.resource_type == "notice",
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_seen is not None:
        return TedIngestResult(source_record_id=None, notice=None)

    normalized = normalize_ted_notice(raw_body, ted_notice_id=ted_notice_id)
    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="TED",
            resource_type="notice",
            source_native_id=ted_notice_id,
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code="CC-BY-4.0",  # TODO(confirm): TED's exact reuse terms
            parse_status="PARSED",
        )
    )
    notice = await upsert_ted_notice(
        conn, normalized=normalized, raw_format=raw_format, source_record_id=source_record_id
    )
    return TedIngestResult(source_record_id=source_record_id, notice=notice)

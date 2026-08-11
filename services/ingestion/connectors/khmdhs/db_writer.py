"""Idempotent canonical writes for the ΚΗΜΔΗΣ connector, shared across all
five resources (request/notice/auction/contract/payment).

True idempotency starts at `source_records`: if the incoming payload's
content_sha256 already exists for (source, resource), nothing else runs —
description.txt §16.2's dedup key is `source + resource + ADAM +
content_hash`, and the ADAM is folded into `source_native_id` alongside the
hash check.

Entity identity and normalized-name resolution lives in
`services/entity_resolution/resolve.py`, shared with every connector.

A PAYMENT (or any other) act arriving before its CONTRACT counterpart is
expected, not an error — §26.1's PARTIAL_LIFECYCLE state, not a fabricated
contract. `adamchain.py` fills in `process_id` and cross-act links
separately, after the act already exists here.

`upsert_act`/`ingest_khmdhs_record` report insert-vs-material-change
(`ActUpsertResult`/`IngestResult`) so `services/alerts/evaluate.py` (Phase E)
can fire `contract.created`/`contract.modified` without re-deriving it from
scratch — see §32.3's material-change definition (amount, status, deadline,
contractor).

`normalized.related_ada` (decisionRelatedAda/contractRelatedAda/
cancellationADA) is deliberately **not** written as an `act_identifiers`
row on this act. Those ΑΔΑ values reference a *different* act — a Διαύγεια
decision — not this one; the identifier belongs on the decision act once
`services/ingestion/connectors/diavgeia` fetches and creates it (act_links
connects the two, confidence 1.0, `EXACT_ADA`). Attaching them here would
collide with `act_identifiers`' global unique index on (scheme, value) the
moment the real decision act tries to claim the same ΑΔΑ. `related_ada` is
exposed on `ActUpsertResult` purely as a trigger list — description.txt
§17.1's "whenever an ΑΔΑ appears in ΚΗΜΔΗΣ, enqueue a Διαύγεια fetch".
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    field_provenance,
    procurement_acts,
    source_records,
)
from services.entity_resolution.resolve import find_or_create_entity_by_afm as _find_or_create_entity_by_afm

from .normalize import NormalizedAct, NormalizedContractAct, NormalizedParty, normalize_adam, normalize_khmdhs_record

# Fields whose change counts as "material" per §32.3 (deadline/amount/status/
# contractor). Compared before vs. after on every update.
_MATERIAL_ACT_FIELDS = (
    "amount_net",
    "amount_gross",
    "status",
    "procedure_type",
    "submission_deadline",
    "end_date",
)


@dataclass(frozen=True)
class ActUpsertResult:
    act_id: uuid.UUID
    act_type: str
    is_new: bool
    changed_fields: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    related_ada: list[str] = field(default_factory=list)  # trigger list for Διαύγεια resolution, see module docstring
    contractor_entity_id: uuid.UUID | None = None  # trigger for ΓΕΜΗ enrichment, see connectors/gemi/resolve.py
    contractor_afm_normalized: str | None = None
    contractor_entities: list[tuple[uuid.UUID, str | None]] = field(default_factory=list)
    funding_ref_candidates: list[tuple[str, str]] = field(default_factory=list)
    # [(field_name, value), ...] trigger list for ΑΝΑΠΤΥΞΗ resolution — both
    # public_funding_ref_ops and espa_fund_program_ref, neither assumed to be
    # the ΟΠΣ/MIS code without verification (§19.4). See connectors/anaptyxi/resolve.py.


@dataclass(frozen=True)
class IngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    adam_normalized: str
    act_upsert: ActUpsertResult | None  # None if deduped


async def load_existing_act_context(
    conn: AsyncConnection,
    *,
    resource: str,
    raw_record: dict[str, Any],
) -> IngestResult:
    """Rebuilds enrichment trigger context for an unchanged source record.

    Daily rolling windows intentionally see the same payload more than once.
    Core ingestion remains a no-op, but provider work may need retrying after
    an earlier outage. This loads the existing canonical act and combines it
    with trigger-only values from the normalized source payload.
    """
    normalized = normalize_khmdhs_record(raw_record, resource=resource)
    adam_normalized = normalize_adam(str(raw_record["referenceNumber"]))
    act_id = (
        await conn.execute(
            select(procurement_acts.c.id)
            .select_from(
                procurement_acts.join(
                    act_identifiers,
                    act_identifiers.c.act_id == procurement_acts.c.id,
                )
            )
            .where(
                act_identifiers.c.scheme == "ADAM",
                act_identifiers.c.value_normalized == adam_normalized,
                procurement_acts.c.is_current.is_(True),
            )
            .limit(1)
        )
    ).scalar()
    if act_id is None:
        return IngestResult(source_record_id=None, adam_normalized=adam_normalized, act_upsert=None)

    contractor_entity_ids = list(
        await conn.execute(
            select(act_parties.c.entity_id)
            .where(
                act_parties.c.act_id == act_id,
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
            )
        )
    )
    contractor_entities: list[tuple[uuid.UUID, str | None]] = []
    for row in contractor_entity_ids:
        afm = (
            await conn.execute(
                select(entity_identifiers.c.value_normalized)
                .where(
                    entity_identifiers.c.entity_id == row.entity_id,
                    entity_identifiers.c.scheme == "AFM",
                    entity_identifiers.c.is_current.is_(True),
                )
                .limit(1)
            )
        ).scalar()
        contractor_entities.append((row.entity_id, afm))
    contractor_entity_id = contractor_entities[0][0] if contractor_entities else None
    funding_ref_candidates = [
        (field_name, value)
        for field_name, value in (
            ("publicFundingRefOps", normalized.public_funding_ref_ops),
            ("espaFundProgramRef", normalized.espa_fund_program_ref),
        )
        if value
    ]
    return IngestResult(
        source_record_id=None,
        adam_normalized=adam_normalized,
        act_upsert=ActUpsertResult(
            act_id=act_id,
            act_type=normalized.act_type,
            is_new=False,
            related_ada=normalized.related_ada,
            contractor_entity_id=contractor_entity_id,
            contractor_afm_normalized=contractor_entities[0][1] if contractor_entities else None,
            contractor_entities=contractor_entities,
            funding_ref_candidates=funding_ref_candidates,
        ),
    )


async def find_or_create_entity_by_afm(
    conn: AsyncConnection,
    *,
    party: NormalizedParty,
    entity_type: str,
    source_record_id: uuid.UUID,
) -> uuid.UUID:
    """Thin adapter over services.entity_resolution.resolve — kept here so
    call sites in this module don't need to unpack NormalizedParty
    themselves. Prefer importing the shared function directly in new code."""
    return await _find_or_create_entity_by_afm(
        conn,
        afm_raw=party.afm_raw,
        afm_normalized=party.afm_normalized,
        afm_checksum_valid=party.afm_checksum_valid,
        name=party.name,
        entity_type=entity_type,
        source_record_id=source_record_id,
    )


async def find_or_create_entity_by_source_native(
    conn: AsyncConnection,
    *,
    source_system: str,
    source_native_id: str,
    name: str | None,
    entity_type: str,
    source_record_id: uuid.UUID,
) -> uuid.UUID:
    """Fallback identity for source-owned organization ids when a record has
    no ΑΦΜ. This is intentionally exact and source-scoped; it is not a fuzzy
    name merge and it can later be merged/repointed if an ΑΦΜ appears."""
    normalized_value = f"{source_system}:{source_native_id}".upper()
    row = (
        await conn.execute(
            select(entity_identifiers.c.entity_id).where(
                entity_identifiers.c.scheme == "SOURCE_NATIVE_ID",
                entity_identifiers.c.value_normalized == normalized_value,
                entity_identifiers.c.is_current.is_(True),
            )
        )
    ).first()
    if row is not None:
        return row.entity_id

    entity_id = uuid.uuid4()
    display_name = name or normalized_value
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
            scheme="SOURCE_NATIVE_ID",
            value_raw=source_native_id,
            value_normalized=normalized_value,
            country_code="GR",
            source_record_id=source_record_id,
            confidence=1,
            identifier_valid=True,
            match_eligibility="ELIGIBLE",
        )
    )
    return entity_id


async def upsert_act(
    conn: AsyncConnection,
    *,
    normalized: NormalizedAct,
    source_record_id: uuid.UUID,
) -> ActUpsertResult:
    existing = await conn.execute(
        select(act_identifiers.c.act_id).where(
            act_identifiers.c.scheme == "ADAM",
            act_identifiers.c.value_normalized == normalized.adam_normalized,
        )
    )
    row = existing.first()
    is_new = row is None
    act_id = row.act_id if row is not None else uuid.uuid4()

    prev_act = None
    prev_supplier_entity_id: uuid.UUID | None = None
    prev_supplier_entity_ids: set[uuid.UUID] = set()
    if not is_new:
        prev_act = (
            await conn.execute(select(procurement_acts).where(procurement_acts.c.id == act_id))
        ).first()
        prev_suppliers = list(
            await conn.execute(
                select(act_parties.c.entity_id).where(
                    act_parties.c.act_id == act_id,
                    act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
                )
            )
        )
        prev_supplier_entity_ids = {supplier.entity_id for supplier in prev_suppliers}
        prev_supplier_entity_id = prev_suppliers[0].entity_id if prev_suppliers else None

    act_values: dict[str, Any] = dict(
        act_type=normalized.act_type,
        title=normalized.title,
        normalized_title=normalized.title.upper() if normalized.title else None,
        publication_date=normalized.publication_date,
        submission_date=normalized.submission_date,
        submission_deadline=normalized.submission_deadline,
        end_date=normalized.end_date,
        amount_net=normalized.amount_net,
        vat_amount=normalized.vat_amount,
        amount_gross=normalized.amount_gross,
        currency=normalized.currency,
        status=None,
        procedure_type=normalized.procedure_type,
        source_details=normalized.source_details,
        source_record_id=source_record_id,
        is_current=True,
        updated_at=datetime.now(timezone.utc),
    )

    changed_fields: dict[str, tuple[Any, Any]] = {}

    if is_new:
        await conn.execute(procurement_acts.insert().values(id=act_id, **act_values))
        await conn.execute(
            act_identifiers.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                scheme="ADAM",
                value_raw=normalized.source_native_id,
                value_normalized=normalized.adam_normalized,
                source_record_id=source_record_id,
            )
        )
    else:
        await conn.execute(
            procurement_acts.update().where(procurement_acts.c.id == act_id).values(**act_values)
        )
        for field_name in _MATERIAL_ACT_FIELDS:
            old_value = getattr(prev_act, field_name)
            new_value = act_values[field_name]
            if old_value != new_value:
                changed_fields[field_name] = (old_value, new_value)

    observed_at = (
        await conn.execute(
            select(source_records.c.fetched_at).where(source_records.c.id == source_record_id)
        )
    ).scalar_one()
    provenance_paths = {
        "act_type": "$resource",
        "title": "$.title",
        "submission_date": "$.submissionDate",
        "end_date": "$.contractEndDate|$.endDate|$.contractDurationEndDate",
        "amount_net": "$.totalCostWithoutVAT|$.amountNet|$.contractValue",
        "vat_amount": "$.vatAmount",
        "amount_gross": "$.totalCostWithVAT|$.amountGross|$.totalCost",
        "currency": "$.currency",
        "procedure_type": "$.procedureType|$.procedureCategory",
    }
    provenance_values = {"act_type": normalized.act_type, **act_values}
    for field_name, source_path in provenance_paths.items():
        value = provenance_values.get(field_name)
        if value is None:
            continue
        await conn.execute(field_provenance.insert().values(
            id=uuid.uuid4(), object_type="procurement_acts", object_id=act_id,
            field_name=field_name, source_record_id=source_record_id,
            source_path=source_path, extraction_method="DIRECT_FIELD_MAPPING",
            confidence=1, observed_at=observed_at,
            value_hash=hashlib.sha256(str(value).encode("utf-8")).hexdigest(),
        ))
    for field_name, values, source_path in (
        ("cpv_codes", normalized.cpv_codes, "$.cpv*"),
        ("nuts_codes", normalized.nuts_codes, "$.nuts*|$.placeOfPerformance*"),
    ):
        if values:
            await conn.execute(field_provenance.insert().values(
                id=uuid.uuid4(), object_type="procurement_acts", object_id=act_id,
                field_name=field_name, source_record_id=source_record_id,
                source_path=source_path, extraction_method="DIRECT_FIELD_MAPPING",
                confidence=1, observed_at=observed_at,
                value_hash=hashlib.sha256("|".join(values).encode("utf-8")).hexdigest(),
            ))

    # CPV / locations / parties: delete-and-reinsert per act. Simplest
    # correct approach for a first version; revisit if per-row provenance
    # history for these specifically (as opposed to the act as a whole)
    # turns out to matter.
    await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id == act_id))
    for index, cpv in enumerate(normalized.cpv_codes):
        await conn.execute(
            act_cpv_codes.insert().values(
                act_id=act_id,
                cpv_code=cpv,
                is_primary=(index == 0),
                source_record_id=source_record_id,
            )
        )

    # Preserve the asynchronously derived place-of-performance rows until the
    # new source version's geo job replaces them. This avoids a blank map in
    # the interval between ingestion and worker completion.
    await conn.execute(
        act_locations.delete().where(
            act_locations.c.act_id == act_id,
            act_locations.c.enrichment_job_id.is_(None),
        )
    )
    for nuts_code in normalized.nuts_codes:
        await conn.execute(
            act_locations.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                nuts_code=nuts_code,
                source_record_id=source_record_id,
            )
        )

    await conn.execute(act_parties.delete().where(act_parties.c.act_id == act_id))
    if normalized.buyer is not None:
        if normalized.buyer.afm_normalized:
            buyer_entity_id = await find_or_create_entity_by_afm(
                conn,
                party=normalized.buyer,
                entity_type="PUBLIC_ORGANIZATION",
                source_record_id=source_record_id,
            )
        elif normalized.buyer.source_native_id:
            buyer_entity_id = await find_or_create_entity_by_source_native(
                conn,
                source_system="KHMDHS_ORG",
                source_native_id=normalized.buyer.source_native_id,
                name=normalized.buyer.name,
                entity_type="PUBLIC_ORGANIZATION",
                source_record_id=source_record_id,
            )
        else:
            buyer_entity_id = None
    else:
        buyer_entity_id = None
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
    contractor_entities: list[tuple[uuid.UUID, str | None]] = []
    seen_contractor_entities: set[uuid.UUID] = set()
    normalized_contractors = normalized.contractors or (
        [normalized.contractor] if normalized.contractor is not None else []
    )
    for contractor in normalized_contractors:
        if contractor.afm_normalized:
            contractor_entity_id = await find_or_create_entity_by_afm(
                conn,
                party=contractor,
                entity_type="COMPANY",
                source_record_id=source_record_id,
            )
        elif contractor.source_native_id:
            contractor_entity_id = await find_or_create_entity_by_source_native(
                conn,
                source_system="KHMDHS_CONTRACTOR",
                source_native_id=contractor.source_native_id,
                name=contractor.name,
                entity_type="COMPANY",
                source_record_id=source_record_id,
            )
        else:
            continue
        if contractor_entity_id in seen_contractor_entities:
            continue
        seen_contractor_entities.add(contractor_entity_id)
        contractor_entities.append((contractor_entity_id, contractor.afm_normalized))
        await conn.execute(
            act_parties.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                entity_id=contractor_entity_id,
                party_role="SUPPLIER",
                amount=contractor.amount,
                source_record_id=source_record_id,
            )
        )
    contractor_entity_id = contractor_entities[0][0] if contractor_entities else None
    if not is_new and prev_supplier_entity_id != contractor_entity_id:
        changed_fields["contractor_entity_id"] = (prev_supplier_entity_id, contractor_entity_id)
    elif not is_new and prev_supplier_entity_ids != seen_contractor_entities:
        changed_fields["contractor_entity_ids"] = (
            sorted(str(value) for value in prev_supplier_entity_ids),
            sorted(str(value) for value in seen_contractor_entities),
        )

    funding_ref_candidates: list[tuple[str, str]] = [
        (field_name, value)
        for field_name, value in (
            ("publicFundingRefOps", normalized.public_funding_ref_ops),
            ("espaFundProgramRef", normalized.espa_fund_program_ref),
        )
        if value
    ]

    return ActUpsertResult(
        act_id=act_id,
        act_type=normalized.act_type,
        is_new=is_new,
        changed_fields=changed_fields,
        related_ada=normalized.related_ada,
        contractor_entity_id=contractor_entity_id,
        contractor_afm_normalized=contractor_entities[0][1] if contractor_entities else None,
        contractor_entities=contractor_entities,
        funding_ref_candidates=funding_ref_candidates,
    )


async def upsert_contract_act(
    conn: AsyncConnection,
    *,
    normalized: NormalizedContractAct,
    source_record_id: uuid.UUID,
) -> uuid.UUID:
    """Backward-compat wrapper — prefer upsert_act(), which returns
    ActUpsertResult instead of a bare id."""
    result = await upsert_act(conn, normalized=normalized, source_record_id=source_record_id)
    return result.act_id


async def ingest_khmdhs_record(
    conn: AsyncConnection,
    *,
    resource: str,
    raw_record: dict[str, Any],
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> IngestResult:
    """`IngestResult.source_record_id` is None if this exact payload was
    already ingested (content_sha256 dedup — the idempotency contract);
    `adam_normalized` is always populated, even on a dedup no-op."""
    adam_normalized = normalize_adam(str(raw_record["referenceNumber"]))

    already_seen = await conn.execute(
        select(source_records.c.id).where(
            source_records.c.source_system == "KHMDHS",
            source_records.c.resource_type == resource,
            source_records.c.content_sha256 == content_sha256,
        )
    )
    if already_seen.first() is not None:
        return IngestResult(source_record_id=None, adam_normalized=adam_normalized, act_upsert=None)

    normalized = normalize_khmdhs_record(raw_record, resource=resource)
    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="KHMDHS",
            resource_type=resource,
            source_native_id=normalized.source_native_id,
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code="CC-BY-4.0",
            parse_status="PARSED",
        )
    )
    act_upsert = await upsert_act(conn, normalized=normalized, source_record_id=source_record_id)
    return IngestResult(source_record_id=source_record_id, adam_normalized=adam_normalized, act_upsert=act_upsert)


async def ingest_contract_record(
    conn: AsyncConnection,
    *,
    raw_record: dict[str, Any],
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> uuid.UUID | None:
    """Backward-compat wrapper — prefer ingest_khmdhs_record(), which
    returns IngestResult instead of a bare id-or-None."""
    result = await ingest_khmdhs_record(
        conn,
        resource="contract",
        raw_record=raw_record,
        payload_uri=payload_uri,
        content_sha256=content_sha256,
        http_status=http_status,
        fetched_at=fetched_at,
    )
    return result.source_record_id

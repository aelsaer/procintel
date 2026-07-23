"""ΜΕΦ expense linkage — description.txt §20.1-20.3's confidence tiers.

Triggered per new/refreshed contractor entity from the ΚΗΜΔΗΣ pipeline (same
trigger point as `connectors/gemi` — `ActUpsertResult.contractor_entity_id`/
`contractor_afm_normalized`): searches ΜΕΦ for expenses to that ΑΦΜ, stores
each, then tries to attach it to a specific ΚΗΜΔΗΣ act using the combination
the spec requires — **never** ΑΦΜ alone (§20.1: "the same company can have
many unrelated transactions"):

    same ΑΔΑ + same ΑΦΜ               -> 0.99  (ADA_AND_AFM)
    same ΑΔΑ + same buyer             -> 0.97  (ADA_AND_BUYER)
    same ΑΦΜ + same amount + ±5 days  -> 0.90  (AFM_AMOUNT_DATE)
    same ΑΦΜ only                     -> candidate, NOT a link

Tier 4 leaves `mef_expenses.linked_act_id` NULL — the expense is still
stored (it's real data), just not connected to any specific procurement
act. UI wording for a real link is enforced at the presentation layer per
§20.3 ("a declared expense was found that possibly relates to...", never
"the contract was paid").
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_identifiers,
    act_links,
    act_parties,
    mef_expenses,
    mef_organizations,
    procurement_acts,
    source_records,
)
from packages.source_clients.raw_store import RawStore
from services.entity_resolution.resolve import find_or_create_entity_by_afm
from services.ingestion.connectors.khmdhs.afm import valid_greek_afm

from .client import MefClient
from .db_writer import ingest_expense_record

AMOUNT_DATE_WINDOW_DAYS = 5
LOOKUP_REFRESH_INTERVAL = timedelta(days=1)

_TIER_CONFIDENCE = {
    "ADA_AND_AFM": Decimal("0.99"),
    "ADA_AND_BUYER": Decimal("0.97"),
    "AFM_AMOUNT_DATE": Decimal("0.90"),
}


async def _last_lookup_at(conn: AsyncConnection, afm_normalized: str) -> datetime | None:
    return (
        await conn.execute(
            select(source_records.c.fetched_at)
            .where(
                source_records.c.source_system == "MEF",
                source_records.c.resource_type == "expense_lookup",
                source_records.c.source_native_id == afm_normalized,
            )
            .order_by(source_records.c.fetched_at.desc())
            .limit(1)
        )
    ).scalar()


async def _record_lookup(conn: AsyncConnection, *, afm_normalized: str, fetched_at: datetime) -> None:
    content_sha256 = hashlib.sha256(
        f"{afm_normalized}:{fetched_at.date().isoformat()}".encode("utf-8")
    ).hexdigest()
    existing = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "MEF",
                source_records.c.resource_type == "expense_lookup",
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if existing is not None:
        return
    await conn.execute(
        source_records.insert().values(
            id=uuid.uuid4(),
            source_system="MEF",
            resource_type="expense_lookup",
            source_native_id=afm_normalized,
            content_sha256=content_sha256,
            payload_uri="",
            fetched_at=fetched_at,
            http_status=200,
            parse_status="PARSED",
        )
    )


async def _resolve_organization_entity(
    conn: AsyncConnection, *, mef_organization_id: uuid.UUID, source_record_id: uuid.UUID
) -> uuid.UUID | None:
    org_row = (
        await conn.execute(select(mef_organizations).where(mef_organizations.c.id == mef_organization_id))
    ).one()
    if org_row.entity_id is not None:
        return org_row.entity_id
    if not org_row.afm_raw:
        return None

    afm_digits = "".join(ch for ch in org_row.afm_raw if ch.isdigit())
    entity_id = await find_or_create_entity_by_afm(
        conn,
        afm_raw=org_row.afm_raw,
        afm_normalized=afm_digits,
        afm_checksum_valid=valid_greek_afm(org_row.afm_raw),
        name=org_row.name,
        entity_type="PUBLIC_ORGANIZATION",
        source_record_id=source_record_id,
    )
    await conn.execute(
        mef_organizations.update().where(mef_organizations.c.id == mef_organization_id).values(entity_id=entity_id)
    )
    return entity_id


async def _find_origin_act_for_ada(conn: AsyncConnection, *, ada_normalized: str) -> uuid.UUID | None:
    """An ΑΔΑ names a Διαύγεια decision act (`act_identifiers`), not the
    ΚΗΜΔΗΣ act itself (§17.1 — see khmdhs/db_writer.py's module docstring).
    Decision acts carry no `act_parties` of their own
    (diavgeia/db_writer.py: "Decisions get no act_parties in this pass") —
    the contractor/buyer to compare against lives on the ΚΗΜΔΗΣ act the
    decision approves, reached via the `APPROVES` `act_links` edge written
    by diavgeia/resolve.py."""
    row = (
        await conn.execute(
            select(act_links.c.to_act_id)
            .select_from(act_identifiers.join(act_links, act_links.c.from_act_id == act_identifiers.c.act_id))
            .where(
                act_identifiers.c.scheme == "ADA",
                act_identifiers.c.value_normalized == ada_normalized,
                act_links.c.link_type == "APPROVES",
            )
            .limit(1)
        )
    ).first()
    return row.to_act_id if row is not None else None


async def _act_has_party(
    conn: AsyncConnection, *, act_id: uuid.UUID, entity_id: uuid.UUID, roles: tuple[str, ...]
) -> bool:
    row = (
        await conn.execute(
            select(act_parties.c.id).where(
                act_parties.c.act_id == act_id,
                act_parties.c.entity_id == entity_id,
                act_parties.c.party_role.in_(roles),
            )
        )
    ).first()
    return row is not None


async def _find_act_by_amount_and_date(
    conn: AsyncConnection,
    *,
    contractor_entity_id: uuid.UUID,
    amount: Decimal | None,
    expense_date: date | None,
) -> uuid.UUID | None:
    if amount is None or expense_date is None:
        return None
    window_start = expense_date - timedelta(days=AMOUNT_DATE_WINDOW_DAYS)
    window_end = expense_date + timedelta(days=AMOUNT_DATE_WINDOW_DAYS)
    row = (
        await conn.execute(
            select(procurement_acts.c.id)
            .select_from(procurement_acts.join(act_parties, act_parties.c.act_id == procurement_acts.c.id))
            .where(
                act_parties.c.entity_id == contractor_entity_id,
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR")),
                procurement_acts.c.amount_gross == amount,
                procurement_acts.c.decision_date.is_not(None),
                procurement_acts.c.decision_date >= window_start,
                procurement_acts.c.decision_date <= window_end,
            )
            .limit(1)
        )
    ).first()
    return row.id if row is not None else None


async def resolve_expense_link(
    conn: AsyncConnection,
    *,
    expense_id: uuid.UUID,
    mef_organization_id: uuid.UUID,
    contractor_entity_id: uuid.UUID,
    related_ada: str | None,
    amount: Decimal | None,
    expense_date: date | None,
    source_record_id: uuid.UUID,
) -> tuple[uuid.UUID | None, str | None, Decimal | None]:
    ada_normalized = related_ada.strip().upper() if related_ada else None
    linked_act_id: uuid.UUID | None = None
    link_method: str | None = None
    confidence: Decimal | None = None

    if ada_normalized:
        origin_act_id = await _find_origin_act_for_ada(conn, ada_normalized=ada_normalized)
        if origin_act_id is not None:
            if await _act_has_party(
                conn, act_id=origin_act_id, entity_id=contractor_entity_id, roles=("SUPPLIER", "CONTRACTOR")
            ):
                linked_act_id = origin_act_id
                link_method, confidence = "ADA_AND_AFM", _TIER_CONFIDENCE["ADA_AND_AFM"]
            else:
                buyer_entity_id = await _resolve_organization_entity(
                    conn, mef_organization_id=mef_organization_id, source_record_id=source_record_id
                )
                if buyer_entity_id is not None and await _act_has_party(
                    conn, act_id=origin_act_id, entity_id=buyer_entity_id, roles=("BUYER", "CONTRACTING_AUTHORITY")
                ):
                    linked_act_id = origin_act_id
                    link_method, confidence = "ADA_AND_BUYER", _TIER_CONFIDENCE["ADA_AND_BUYER"]

    if linked_act_id is None:
        linked_act_id = await _find_act_by_amount_and_date(
            conn, contractor_entity_id=contractor_entity_id, amount=amount, expense_date=expense_date
        )
        if linked_act_id is not None:
            link_method, confidence = "AFM_AMOUNT_DATE", _TIER_CONFIDENCE["AFM_AMOUNT_DATE"]

    # Tier 4 (AFM-only, no linked_act_id/link_method/confidence) is the
    # implicit fallback — every expense here already matched by recipient
    # ΑΦΜ (the search precondition), so "no tier matched" simply means "not
    # a link", per §20.2. The resolved recipient entity is still recorded.
    values = dict(recipient_entity_id=contractor_entity_id)
    if linked_act_id is not None:
        values.update(linked_act_id=linked_act_id, link_method=link_method, confidence=confidence)
    await conn.execute(mef_expenses.update().where(mef_expenses.c.id == expense_id).values(**values))

    return linked_act_id, link_method, confidence


async def resolve_expenses_for_contractor(
    conn: AsyncConnection,
    *,
    client: MefClient,
    raw_store: RawStore,
    contractor_entity_id: uuid.UUID,
    afm_normalized: str,
) -> int:
    """Fetches ΜΕΦ expenses for this ΑΦΜ, stores each, and attempts to link
    each to a specific ΚΗΜΔΗΣ act per the tiered confidence rules above.
    Returns how many expenses were newly ingested (not deduped)."""
    now = datetime.now(timezone.utc)
    last_lookup_at = await _last_lookup_at(conn, afm_normalized)
    if last_lookup_at is not None and now - last_lookup_at < LOOKUP_REFRESH_INTERVAL:
        return 0

    response = await client.find_expenses_by_recipient_afm(afm_normalized)

    ingested_count = 0
    for expense_raw in response.expenses:
        payload = json.dumps(expense_raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        raw_ref = await raw_store.put(
            source="mef", resource="expense", partition_key=f"afm={afm_normalized}", payload=payload
        )
        ingest_result = await ingest_expense_record(
            conn,
            recipient_afm=afm_normalized,
            raw_body=expense_raw,
            payload_uri=raw_ref.payload_uri,
            content_sha256=raw_ref.content_sha256,
            http_status=response.http_status,
            fetched_at=now,
        )
        if ingest_result.source_record_id is None:
            continue
        ingested_count += 1
        await resolve_expense_link(
            conn,
            expense_id=ingest_result.expense_id,
            mef_organization_id=ingest_result.mef_organization_id,
            contractor_entity_id=contractor_entity_id,
            related_ada=ingest_result.related_ada,
            amount=ingest_result.amount,
            expense_date=ingest_result.expense_date,
            source_record_id=ingest_result.source_record_id,
        )

    await _record_lookup(conn, afm_normalized=afm_normalized, fetched_at=now)
    await conn.commit()
    return ingested_count


async def relink_existing_expenses_for_contractor(
    conn: AsyncConnection,
    *,
    contractor_entity_id: uuid.UUID,
    afm_normalized: str,
) -> int:
    """Retry local linkage for stored, currently unlinked MEF expenses."""
    afm_digits = "".join(character for character in afm_normalized if character.isdigit())
    rows = (
        await conn.execute(
            select(mef_expenses).where(
                mef_expenses.c.linked_act_id.is_(None),
                mef_expenses.c.source_record_id.is_not(None),
                or_(
                    mef_expenses.c.recipient_entity_id == contractor_entity_id,
                    func.regexp_replace(
                        func.coalesce(mef_expenses.c.recipient_afm_raw, ""),
                        "[^0-9]",
                        "",
                        "g",
                    )
                    == afm_digits,
                ),
            )
        )
    ).all()

    linked = 0
    for row in rows:
        linked_act_id, _, _ = await resolve_expense_link(
            conn,
            expense_id=row.id,
            mef_organization_id=row.mef_organization_id,
            contractor_entity_id=contractor_entity_id,
            related_ada=row.related_ada_raw,
            amount=row.amount,
            expense_date=row.expense_date,
            source_record_id=row.source_record_id,
        )
        if linked_act_id is not None:
            linked += 1

    if rows:
        await conn.commit()
    return linked

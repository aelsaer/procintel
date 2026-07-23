"""adamChain resolution + process grouping — description.txt §16.5-16.6.

For every new/changed ΑΔΑΜ, `adamChain` is called; its response is stored as
its own raw/`source_records` entry; every related ΑΔΑΜ is linked
(`act_links`, `link_method='ADAMCHAIN'`, `confidence=1.000`); and the acts
involved are grouped into a stable `procurement_processes` row via
`process_members` — merging two existing processes, with full audit trail,
if the chain reveals they're actually the same procurement (§16.6).

Two things here are best-effort pending confirmation against the live API
(docs/source-contracts/khmdhs.md, Στάδιο 0):

1. The adamChain response shape — `_extract_chain_adams` tolerates a few
   plausible envelope shapes (flat string list, or a list of dicts with a
   `referenceNumber` field, under a few plausible top-level keys), but the
   real shape needs confirming and this function fixing to match.
2. What relationship *type* connects two ΑΔΑΜ in the chain (APPROVES,
   AWARDS, EXECUTES, ...). Without a confirmed response structure that
   states this per-pair, every chain member is linked to the seed act with
   `link_type='RELATED_TO'` — correct and safe (never asserts a relationship
   stronger than what's known), but coarser than the ideal §15.7 link-type
   vocabulary. Tighten this once the real response is confirmed.

ΑΔΑΜ category inference (`infer_act_type_from_adam`) is *not* a guess — it's
literally spec'd in §7.1 (REQ/PROC/AWRD/SYMV/PAY segments) and is only used
to backfill a placeholder act's act_type before its own resource connector
has ingested it; a real ingested act's act_type is never overwritten by it.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_identifiers,
    act_links,
    act_parties,
    procurement_acts,
    procurement_processes,
    process_members,
    process_merge_log,
    source_records,
)
from packages.source_clients.raw_store import RawStore
from services.alerts.delivery import DeliveryChannel
from services.alerts.evaluate import evaluate_buyer_new_procurement_and_fire

from .client import KhmdhsClient
from .normalize import normalize_adam

_ADAM_CATEGORY_TO_ACT_TYPE: dict[str, str] = {
    "REQ": "REQUEST",
    "PROC": "NOTICE",
    "AWRD": "AWARD",
    "SYMV": "CONTRACT",
    "PAY": "PAYMENT",
}

_SUMMARY_ACT_PRIORITY = {
    "CONTRACT": 0,
    "AWARD": 1,
    "NOTICE": 2,
    "REQUEST": 3,
    "PAYMENT": 4,
}


def infer_act_type_from_adam(adam_normalized: str) -> str:
    for token, act_type in _ADAM_CATEGORY_TO_ACT_TYPE.items():
        if token in adam_normalized:
            return act_type
    return "UNKNOWN"


def _extract_chain_adams(raw_response: Any) -> list[str]:
    if isinstance(raw_response, list):
        items = raw_response
    elif isinstance(raw_response, dict):
        items = []
        for key in ("requests", "approvedRequests", "notices", "auctions", "contracts", "payments"):
            bucket = raw_response.get(key)
            if isinstance(bucket, list):
                items.extend(bucket)
        if not items:
            items = (
                raw_response.get("relatedRecords")
                or raw_response.get("relatedAdams")
                or raw_response.get("chain")
                or raw_response.get("data")
                or []
            )
    else:
        items = []

    adams: list[str] = []
    for item in items:
        if isinstance(item, str):
            adams.append(item.rstrip("*"))
        elif isinstance(item, dict):
            value = item.get("referenceNumber") or item.get("adam")
            if value:
                adams.append(str(value).rstrip("*"))
    return [normalize_adam(a) for a in adams if a]


async def get_act_id_by_adam(conn: AsyncConnection, adam_normalized: str) -> uuid.UUID | None:
    row = (
        await conn.execute(
            select(act_identifiers.c.act_id).where(
                act_identifiers.c.scheme == "ADAM",
                act_identifiers.c.value_normalized == adam_normalized,
            )
        )
    ).first()
    return row.act_id if row is not None else None


async def _ensure_act_for_adam(
    conn: AsyncConnection, *, adam_normalized: str, fallback_source_record_id: uuid.UUID
) -> uuid.UUID:
    existing_act_id = await get_act_id_by_adam(conn, adam_normalized)
    if existing_act_id is not None:
        return existing_act_id

    act_id = uuid.uuid4()
    await conn.execute(
        procurement_acts.insert().values(
            id=act_id,
            act_type=infer_act_type_from_adam(adam_normalized),
            source_record_id=fallback_source_record_id,
        )
    )
    await conn.execute(
        act_identifiers.insert().values(
            id=uuid.uuid4(),
            act_id=act_id,
            scheme="ADAM",
            value_raw=adam_normalized,
            value_normalized=adam_normalized,
            source_record_id=fallback_source_record_id,
        )
    )
    return act_id


async def _link_chain(
    conn: AsyncConnection,
    *,
    seed_act_id: uuid.UUID,
    related_act_ids: list[uuid.UUID],
    source_record_id: uuid.UUID,
) -> None:
    for related_act_id in related_act_ids:
        if related_act_id == seed_act_id:
            continue
        already_linked = (
            await conn.execute(
                select(act_links.c.id).where(
                    act_links.c.from_act_id == seed_act_id,
                    act_links.c.to_act_id == related_act_id,
                    act_links.c.link_type == "RELATED_TO",
                )
            )
        ).first()
        if already_linked is not None:
            continue
        await conn.execute(
            act_links.insert().values(
                id=uuid.uuid4(),
                from_act_id=seed_act_id,
                to_act_id=related_act_id,
                link_type="RELATED_TO",
                link_method="ADAMCHAIN",
                confidence=1.000,
                evidence={"source": "adamChain", "source_record_id": str(source_record_id)},
                created_by="services.ingestion.connectors.khmdhs.adamchain",
            )
        )


async def _merge_process(conn: AsyncConnection, *, survivor_id: uuid.UUID, merged_id: uuid.UUID) -> None:
    if survivor_id == merged_id:
        return

    already_merged = (
        await conn.execute(
            select(procurement_processes.c.record_status).where(procurement_processes.c.id == merged_id)
        )
    ).first()
    if already_merged is not None and already_merged.record_status == "MERGED":
        return  # idempotent: a previous run already merged this pair

    member_rows = (
        await conn.execute(select(process_members.c.act_id).where(process_members.c.process_id == merged_id))
    ).all()
    for member in member_rows:
        exists = (
            await conn.execute(
                select(process_members.c.id).where(
                    process_members.c.process_id == survivor_id,
                    process_members.c.act_id == member.act_id,
                )
            )
        ).first()
        if exists is None:
            await conn.execute(
                process_members.insert().values(
                    id=uuid.uuid4(), process_id=survivor_id, act_id=member.act_id, added_via="MERGE"
                )
            )
        # keep the denormalized pointer (procurement_acts.process_id, read by
        # db/marts/procurement_360.sql) in sync with process_members — a
        # repointed act must stop reporting under the merged-away process.
        await conn.execute(
            procurement_acts.update()
            .where(procurement_acts.c.id == member.act_id)
            .values(process_id=survivor_id)
        )
    await conn.execute(process_members.delete().where(process_members.c.process_id == merged_id))

    await conn.execute(
        procurement_processes.update()
        .where(procurement_processes.c.id == merged_id)
        .values(record_status="MERGED", merged_into_process_id=survivor_id)
    )
    await conn.execute(
        process_merge_log.insert().values(
            id=uuid.uuid4(),
            surviving_process_id=survivor_id,
            merged_process_id=merged_id,
            merge_reason="adamChain revealed a shared act across two previously separate processes",
            evidence={"link_method": "ADAMCHAIN"},
            performed_by="services.ingestion.connectors.khmdhs.adamchain",
        )
    )


async def _assign_process(conn: AsyncConnection, *, act_ids: list[uuid.UUID]) -> tuple[uuid.UUID, bool]:
    """§16.6: process_id is a stable internal UUID, never derived from any
    single ΑΔΑΜ. Zero existing processes among these acts -> create one.
    One -> extend it. Two or more -> controlled, audited, reversible merge
    onto the earliest-observed survivor. Returns `(process_id, is_new)` —
    `is_new` is the trigger `resolve_adam_chain_for_act` uses to fire
    `buyer.new_procurement` (§30.5); a merge or an extension of an existing
    process is not "new"."""
    member_rows = (
        await conn.execute(select(process_members.c.act_id, process_members.c.process_id).where(
            process_members.c.act_id.in_(act_ids)
        ))
    ).all()
    existing_process_ids = {r.process_id for r in member_rows}
    acts_already_members = {r.act_id for r in member_rows}
    is_new_process = not existing_process_ids

    if not existing_process_ids:
        process_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        await conn.execute(
            procurement_processes.insert().values(
                id=process_id,
                public_id=f"proc_{uuid.uuid4().hex[:20]}",
                first_observed_at=now,
                last_observed_at=now,
            )
        )
        survivor_id = process_id
    elif len(existing_process_ids) == 1:
        survivor_id = next(iter(existing_process_ids))
    else:
        rows = (
            await conn.execute(
                select(procurement_processes.c.id, procurement_processes.c.first_observed_at).where(
                    procurement_processes.c.id.in_(existing_process_ids)
                )
            )
        ).all()
        ranked = sorted(
            rows,
            key=lambda r: r.first_observed_at or datetime.min.replace(tzinfo=timezone.utc),
        )
        survivor_id = ranked[0].id
        for other in ranked[1:]:
            await _merge_process(conn, survivor_id=survivor_id, merged_id=other.id)

    for act_id in act_ids:
        if act_id not in acts_already_members:
            await conn.execute(
                process_members.insert().values(
                    id=uuid.uuid4(), process_id=survivor_id, act_id=act_id, added_via="ADAMCHAIN"
                )
            )
        # process_members is the audit trail; procurement_acts.process_id is
        # the denormalized pointer db/marts/procurement_360.sql actually
        # reads. Keep both in sync — including for acts that were already
        # members, so a merge's repointing (above) is reflected here too.
        await conn.execute(
            procurement_acts.update().where(procurement_acts.c.id == act_id).values(process_id=survivor_id)
        )

    return survivor_id, is_new_process


async def _buyer_entity_id_for_act(conn: AsyncConnection, act_id: uuid.UUID) -> uuid.UUID | None:
    row = (
        await conn.execute(
            select(act_parties.c.entity_id).where(
                act_parties.c.act_id == act_id, act_parties.c.party_role == "BUYER"
            )
        )
    ).first()
    return row.entity_id if row is not None else None


async def _sync_process_summary(conn: AsyncConnection, process_id: uuid.UUID) -> None:
    act_rows = (
        await conn.execute(select(procurement_acts).where(procurement_acts.c.process_id == process_id))
    ).all()
    if not act_rows:
        return

    title_source = min(
        (row for row in act_rows if row.title),
        key=lambda row: (
            _SUMMARY_ACT_PRIORITY.get(row.act_type, 99),
            row.submission_date or row.publication_date or row.decision_date or date.max,
        ),
        default=None,
    )
    buyer_entity_id = (
        await conn.execute(
            select(act_parties.c.entity_id)
            .select_from(act_parties.join(procurement_acts, procurement_acts.c.id == act_parties.c.act_id))
            .where(
                procurement_acts.c.process_id == process_id,
                act_parties.c.party_role.in_(("BUYER", "CONTRACTING_AUTHORITY")),
            )
            .limit(1)
        )
    ).scalar()
    estimated_value = next(
        (row.amount_gross for row in act_rows if row.act_type in {"REQUEST", "NOTICE"} and row.amount_gross is not None),
        None,
    )
    awarded_value = next(
        (row.amount_gross for row in act_rows if row.act_type == "AWARD" and row.amount_gross is not None),
        None,
    )
    current_contract_value = next(
        (row.amount_gross for row in act_rows if row.act_type == "CONTRACT" and row.amount_gross is not None),
        None,
    )

    values: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if title_source is not None:
        values["title"] = title_source.title
        values["normalized_title"] = title_source.normalized_title
    if buyer_entity_id is not None:
        values["buyer_entity_id"] = buyer_entity_id
    if estimated_value is not None:
        values["estimated_value"] = estimated_value
    if awarded_value is not None:
        values["awarded_value"] = awarded_value
    if current_contract_value is not None:
        values["current_contract_value"] = current_contract_value
    if current_contract_value is not None:
        values["lifecycle_status"] = "CONTRACTED"
    elif awarded_value is not None:
        values["lifecycle_status"] = "AWARDED"
    elif estimated_value is not None:
        values["lifecycle_status"] = "TENDERING"

    if len(values) > 1:
        await conn.execute(
            procurement_processes.update().where(procurement_processes.c.id == process_id).values(**values)
        )


async def resolve_adam_chain_for_act(
    conn: AsyncConnection,
    *,
    client: KhmdhsClient,
    raw_store: RawStore,
    seed_adam_normalized: str,
    delivery_channel: DeliveryChannel | None = None,
) -> uuid.UUID | None:
    """Fetches, stores, and applies one ΑΔΑΜ's adamChain. Returns the
    resulting process_id, or None if the seed act itself doesn't exist yet
    (shouldn't happen when called right after that act's own ingestion, but
    guards against being called out of order). `delivery_channel` is
    optional and off by default (unlike every existing caller, which
    doesn't pass it) — when given, fires `buyer.new_procurement` (§30.5)
    if this call resulted in a genuinely *new* `procurement_processes` row
    (never for an existing process being extended or merged into)."""
    seed_act_id = await get_act_id_by_adam(conn, seed_adam_normalized)
    if seed_act_id is None:
        return None

    response = await client.fetch_adam_chain(seed_adam_normalized)

    raw_ref = await raw_store.put(
        source="khmdhs",
        resource="adamChain",
        partition_key=f"adam={seed_adam_normalized}",
        payload=response.raw_body or json.dumps(response.body).encode("utf-8"),
    )

    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "KHMDHS",
                source_records.c.resource_type == "adamChain",
                source_records.c.content_sha256 == raw_ref.content_sha256,
            )
        )
    ).first()

    if already_seen is not None:
        # Same chain content already fully processed in an earlier run —
        # process assignment for the seed act is already settled.
        member_row = (
            await conn.execute(
                select(process_members.c.process_id).where(process_members.c.act_id == seed_act_id)
            )
        ).first()
        if member_row is not None:
            await _sync_process_summary(conn, member_row.process_id)
            await conn.commit()
            return member_row.process_id
        return None

    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="KHMDHS",
            resource_type="adamChain",
            source_native_id=seed_adam_normalized,
            content_sha256=raw_ref.content_sha256,
            payload_uri=raw_ref.payload_uri,
            fetched_at=datetime.now(timezone.utc),
            http_status=response.http_status,
            license_code="CC-BY-4.0",
            parse_status="PARSED",
        )
    )

    chain_adams = _extract_chain_adams(response.body)
    related_act_ids = [
        await _ensure_act_for_adam(conn, adam_normalized=adam, fallback_source_record_id=source_record_id)
        for adam in chain_adams
        if adam != seed_adam_normalized
    ]

    await _link_chain(
        conn, seed_act_id=seed_act_id, related_act_ids=related_act_ids, source_record_id=source_record_id
    )

    all_act_ids = [seed_act_id, *related_act_ids]
    process_id, is_new_process = await _assign_process(conn, act_ids=all_act_ids)
    await _sync_process_summary(conn, process_id)

    if is_new_process and delivery_channel is not None:
        buyer_entity_id = await _buyer_entity_id_for_act(conn, seed_act_id)
        if buyer_entity_id is not None:
            await evaluate_buyer_new_procurement_and_fire(
                conn, process_id=process_id, buyer_entity_id=buyer_entity_id, delivery_channel=delivery_channel
            )

    await conn.commit()
    return process_id

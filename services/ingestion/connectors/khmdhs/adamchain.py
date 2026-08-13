"""adamChain resolution + process grouping — description.txt §16.5-16.6.

For every new/changed ΑΔΑΜ, `adamChain` is called; its response is stored as
its own raw/`source_records` entry; every related ΑΔΑΜ is linked
(`act_links`, `link_method='ADAMCHAIN'`, `confidence=1.000`); and the acts
involved are grouped into a stable `procurement_processes` row via
`process_members` — merging two existing processes, with full audit trail,
if the chain reveals they're actually the same procurement (§16.6).

The live envelope is bucketed as `requests`, `approvedRequests`, `notices`,
`auctions`, `contracts` and `payments`. Bucket order produces typed
`APPROVES`, `ANNOUNCES`, `AWARDS`, `EXECUTES` and `PAYS` edges; legacy
flat/dict variants remain accepted for replay compatibility. Modification
markers are retained without asserting a stronger relationship than the
source exposes.

ΑΔΑΜ category inference (`infer_act_type_from_adam`) is *not* a guess — it's
literally spec'd in §7.1 (REQ/PROC/AWRD/SYMV/PAY segments) and is only used
to backfill a placeholder act's act_type before its own resource connector
has ingested it; a real ingested act's act_type is never overwritten by it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
from services.alerts.evaluate import evaluate_buyer_new_procurement_for_all_tenants

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


_CHAIN_BUCKETS = (
    "requests",
    "approvedRequests",
    "notices",
    "auctions",
    "contracts",
    "payments",
)

_BUCKET_ACT_TYPE = {
    "requests": "REQUEST",
    "approvedRequests": "REQUEST",
    "notices": "NOTICE",
    "auctions": "AWARD",
    "contracts": "CONTRACT",
    "payments": "PAYMENT",
}


@dataclass(frozen=True)
class ChainEntry:
    adam: str
    bucket: str
    marker: str | None = None


def _extract_chain_entries(raw_response: Any) -> list[ChainEntry]:
    if isinstance(raw_response, list):
        raw_response = {"requests": raw_response}
    if not isinstance(raw_response, dict):
        return []
    if not any(key in raw_response for key in _CHAIN_BUCKETS):
        raw_response = {
            "requests": (
                raw_response.get("relatedRecords")
                or raw_response.get("relatedAdams")
                or raw_response.get("chain")
                or raw_response.get("data")
                or []
            )
        }
    entries: list[ChainEntry] = []
    for bucket_name in _CHAIN_BUCKETS:
        items = raw_response.get(bucket_name) or []
        if not isinstance(items, list):
            continue
        for item in items:
            value = (
                item
                if isinstance(item, str)
                else item.get("referenceNumber") or item.get("adam")
                if isinstance(item, dict)
                else None
            )
            if not value:
                continue
            raw_value = str(value).strip()
            suffix_length = len(raw_value) - len(raw_value.rstrip("*"))
            marker = (
                "CANCELLATION"
                if suffix_length >= 2
                else "MODIFICATION_OR_EXTENSION"
                if suffix_length == 1
                else None
            )
            entries.append(
                ChainEntry(
                    adam=normalize_adam(raw_value.rstrip("*")),
                    bucket=bucket_name,
                    marker=marker,
                )
            )
    return entries


def _extract_chain_adams(raw_response: Any) -> list[str]:
    return list(dict.fromkeys(entry.adam for entry in _extract_chain_entries(raw_response)))


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
    conn: AsyncConnection,
    *,
    adam_normalized: str,
    fallback_source_record_id: uuid.UUID,
    act_type: str | None = None,
) -> uuid.UUID:
    existing_act_id = await get_act_id_by_adam(conn, adam_normalized)
    if existing_act_id is not None:
        return existing_act_id

    act_id = uuid.uuid4()
    await conn.execute(
        procurement_acts.insert().values(
            id=act_id,
            act_type=act_type or infer_act_type_from_adam(adam_normalized),
            source_record_id=fallback_source_record_id,
            is_current=False,
            source_details={
                "placeholder_state": "EVIDENCE_ONLY",
                "placeholder_reason": "identifier-only adamChain lifecycle evidence",
            },
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


async def _ensure_link(
    conn: AsyncConnection,
    *,
    from_act_id: uuid.UUID,
    to_act_id: uuid.UUID,
    link_type: str,
    source_record_id: uuid.UUID,
    evidence: dict[str, Any] | None = None,
) -> None:
    if from_act_id == to_act_id:
        return
    await conn.execute(
        pg_insert(act_links)
        .values(
            id=uuid.uuid4(),
            from_act_id=from_act_id,
            to_act_id=to_act_id,
            link_type=link_type,
            link_method="ADAMCHAIN",
            confidence=1.000,
            evidence={
                "source": "adamChain",
                "source_record_id": str(source_record_id),
                **(evidence or {}),
            },
            created_by="services.ingestion.connectors.khmdhs.adamchain",
        )
        .on_conflict_do_nothing(
            index_elements=[act_links.c.from_act_id, act_links.c.to_act_id, act_links.c.link_type]
        )
    )


async def _ensure_adamchain_source_record(
    conn: AsyncConnection,
    *,
    seed_adam_normalized: str,
    content_sha256: str,
    payload_uri: str,
    http_status: int,
) -> tuple[uuid.UUID, bool]:
    """Atomically store immutable chain evidence and report whether it was new."""
    candidate_id = uuid.uuid4()
    inserted_id = (
        await conn.execute(
            pg_insert(source_records)
            .values(
                id=candidate_id,
                source_system="KHMDHS",
                resource_type="adamChain",
                source_native_id=seed_adam_normalized,
                content_sha256=content_sha256,
                payload_uri=payload_uri,
                fetched_at=datetime.now(timezone.utc),
                http_status=http_status,
                license_code="CC-BY-4.0",
                parse_status="PARSED",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    source_records.c.source_system,
                    source_records.c.resource_type,
                    source_records.c.content_sha256,
                ]
            )
            .returning(source_records.c.id)
        )
    ).scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id, True

    existing_id = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "KHMDHS",
                source_records.c.resource_type == "adamChain",
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).scalar_one()
    return existing_id, False


async def _link_chain(
    conn: AsyncConnection,
    *,
    seed_act_id: uuid.UUID,
    entries: list[ChainEntry],
    act_ids_by_adam: dict[str, uuid.UUID],
    source_record_id: uuid.UUID,
) -> None:
    by_bucket: dict[str, list[ChainEntry]] = {
        bucket: [entry for entry in entries if entry.bucket == bucket]
        for bucket in _CHAIN_BUCKETS
    }

    async def link_buckets(
        from_bucket: str,
        to_bucket: str,
        link_type: str,
        *,
        reverse: bool = False,
    ) -> None:
        for from_entry in by_bucket[from_bucket]:
            for to_entry in by_bucket[to_bucket]:
                first, second = (
                    (to_entry, from_entry) if reverse else (from_entry, to_entry)
                )
                await _ensure_link(
                    conn,
                    from_act_id=act_ids_by_adam[first.adam],
                    to_act_id=act_ids_by_adam[second.adam],
                    link_type=link_type,
                    source_record_id=source_record_id,
                    evidence={
                        "from_bucket": first.bucket,
                        "to_bucket": second.bucket,
                        "marker": from_entry.marker or to_entry.marker,
                    },
                )

    await link_buckets("approvedRequests", "requests", "APPROVES")
    request_bucket = "approvedRequests" if by_bucket["approvedRequests"] else "requests"
    await link_buckets(request_bucket, "notices", "ANNOUNCES")
    await link_buckets("notices", "auctions", "AWARDS", reverse=True)
    await link_buckets("auctions", "contracts", "EXECUTES")
    await link_buckets("contracts", "payments", "PAYS", reverse=True)

    # A trailing star in the official response marks a modification/extension;
    # two stars mark cancellation. Relate it to the preceding item in the same
    # lifecycle bucket when the source provides one.
    for bucket_name, bucket_entries in by_bucket.items():
        for index, entry in enumerate(bucket_entries):
            if entry.marker is None or index == 0:
                continue
            previous = bucket_entries[index - 1]
            await _ensure_link(
                conn,
                from_act_id=act_ids_by_adam[entry.adam],
                to_act_id=act_ids_by_adam[previous.adam],
                link_type="CANCELS" if entry.marker == "CANCELLATION" else "AMENDS",
                source_record_id=source_record_id,
                evidence={"bucket": bucket_name, "marker": entry.marker},
            )

    linked_ids = set(act_ids_by_adam.values())
    for related_act_id in linked_ids:
        if related_act_id == seed_act_id:
            continue
        has_typed_link = (
            await conn.execute(
                select(act_links.c.id).where(
                    sa.or_(
                        sa.and_(
                            act_links.c.from_act_id == seed_act_id,
                            act_links.c.to_act_id == related_act_id,
                        ),
                        sa.and_(
                            act_links.c.from_act_id == related_act_id,
                            act_links.c.to_act_id == seed_act_id,
                        ),
                    ),
                    act_links.c.link_method == "ADAMCHAIN",
                )
            )
        ).first()
        if has_typed_link is None:
            await _ensure_link(
                conn,
                from_act_id=seed_act_id,
                to_act_id=related_act_id,
                link_type="RELATED_TO",
                source_record_id=source_record_id,
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

    source_record_id, source_record_is_new = await _ensure_adamchain_source_record(
        conn,
        seed_adam_normalized=seed_adam_normalized,
        content_sha256=raw_ref.content_sha256,
        payload_uri=raw_ref.payload_uri,
        http_status=response.http_status,
    )

    if not source_record_is_new:
        # Same chain content already fully processed in an earlier run —
        # process assignment for this seed act may already be settled. Empty
        # chains from different ADAMs legitimately have identical payloads,
        # so reuse the evidence row but still process an unassigned seed.
        member_row = (
            await conn.execute(
                select(process_members.c.process_id).where(process_members.c.act_id == seed_act_id)
            )
        ).first()
        if member_row is not None:
            await _sync_process_summary(conn, member_row.process_id)
            await conn.commit()
            return member_row.process_id

    chain_entries = _extract_chain_entries(response.body)
    if not any(entry.adam == seed_adam_normalized for entry in chain_entries):
        chain_entries.append(
            ChainEntry(
                adam=seed_adam_normalized,
                bucket=next(
                    (
                        bucket
                        for bucket, act_type in _BUCKET_ACT_TYPE.items()
                        if act_type == infer_act_type_from_adam(seed_adam_normalized)
                    ),
                    "requests",
                ),
            )
        )
    act_ids_by_adam: dict[str, uuid.UUID] = {seed_adam_normalized: seed_act_id}
    for entry in chain_entries:
        if entry.adam in act_ids_by_adam:
            continue
        act_ids_by_adam[entry.adam] = await _ensure_act_for_adam(
            conn,
            adam_normalized=entry.adam,
            fallback_source_record_id=source_record_id,
            act_type=_BUCKET_ACT_TYPE[entry.bucket],
        )

    await _link_chain(
        conn,
        seed_act_id=seed_act_id,
        entries=chain_entries,
        act_ids_by_adam=act_ids_by_adam,
        source_record_id=source_record_id,
    )

    all_act_ids = list(dict.fromkeys(act_ids_by_adam.values()))
    process_id, is_new_process = await _assign_process(conn, act_ids=all_act_ids)
    await _sync_process_summary(conn, process_id)

    if is_new_process and delivery_channel is not None:
        buyer_entity_id = await _buyer_entity_id_for_act(conn, seed_act_id)
        if buyer_entity_id is not None:
            await evaluate_buyer_new_procurement_for_all_tenants(
                conn, process_id=process_id, buyer_entity_id=buyer_entity_id, delivery_channel=delivery_channel
            )

    await conn.commit()
    return process_id

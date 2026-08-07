"""Reads `procurement_acts` (+ identifiers/CPV/locations/parties) from
Postgres and bulk-indexes them into OpenSearch.

Per-act related-row lookups (identifiers/parties) follow the same
simplest-correct N+1 style `apps/api/queries.py` already uses rather than
one large aggregating JOIN — consistent with this codebase's existing
pattern, revisit only if reindexing performance at real data volume
becomes a problem.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_cpv_codes,
    act_identifiers,
    act_locations,
    act_parties,
    entities,
    procurement_acts,
)

from .catalog import CATALOGS, reindex_catalogs
from .client import (
    bulk_index,
    create_index,
    delete_index,
    index_count,
    refresh_index,
    swap_index_aliases,
)
from .config import OpenSearchConfig
from .document import ActForIndexing, build_act_document
from .mapping import PROCUREMENT_ACTS_MAPPING

DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class ReindexResult:
    acts_indexed: int
    catalogs: dict[str, int] | None = None


@dataclass(frozen=True)
class AtomicReindexResult:
    acts_indexed: int
    catalogs: dict[str, int]
    aliases: dict[str, str]
    build_id: str


async def _load_acts_for_indexing(
    conn: AsyncConnection,
    act_rows: list[Any],
) -> list[ActForIndexing]:
    if not act_rows:
        return []
    act_ids = [row.id for row in act_rows]
    identifiers_by_act: dict[uuid.UUID, dict[str, list[str]]] = {}
    for row in (
        await conn.execute(
            select(
                act_identifiers.c.act_id,
                act_identifiers.c.scheme,
                act_identifiers.c.value_normalized,
            ).where(act_identifiers.c.act_id.in_(act_ids))
        )
    ).all():
        identifiers_by_act.setdefault(row.act_id, {}).setdefault(
            row.scheme, []
        ).append(row.value_normalized)

    buyers: dict[uuid.UUID, dict[str, str]] = {}
    suppliers: dict[uuid.UUID, list[dict[str, str]]] = {}
    for row in (
        await conn.execute(
            select(
                act_parties.c.act_id,
                act_parties.c.party_role,
                entities.c.id.label("entity_id"),
                entities.c.canonical_name,
            )
            .select_from(
                act_parties.join(entities, entities.c.id == act_parties.c.entity_id)
            )
            .where(act_parties.c.act_id.in_(act_ids))
        )
    ).all():
        party = {"id": str(row.entity_id), "name": row.canonical_name}
        if row.party_role in ("BUYER", "CONTRACTING_AUTHORITY"):
            buyers[row.act_id] = party
        elif row.party_role in ("SUPPLIER", "CONTRACTOR"):
            suppliers.setdefault(row.act_id, []).append(party)

    cpv_by_act: dict[uuid.UUID, list[str]] = {}
    for row in (
        await conn.execute(
            select(act_cpv_codes.c.act_id, act_cpv_codes.c.cpv_code).where(
                act_cpv_codes.c.act_id.in_(act_ids)
            )
        )
    ).all():
        cpv_by_act.setdefault(row.act_id, []).append(row.cpv_code)

    nuts_by_act: dict[uuid.UUID, list[str]] = {}
    for row in (
        await conn.execute(
            select(act_locations.c.act_id, act_locations.c.nuts_code).where(
                act_locations.c.act_id.in_(act_ids),
                act_locations.c.nuts_code.is_not(None),
            )
        )
    ).all():
        nuts_by_act.setdefault(row.act_id, []).append(row.nuts_code)

    documents: list[ActForIndexing] = []
    for act_row in act_rows:
        identifiers = identifiers_by_act.get(act_row.id, {})
        buyer = buyers.get(act_row.id)
        act_suppliers = suppliers.get(act_row.id, [])
        documents.append(
            ActForIndexing(
                id=str(act_row.id),
                process_id=str(act_row.process_id) if act_row.process_id else None,
                adam=(identifiers.get("ADAM") or [None])[0],
                ada_list=identifiers.get("ADA", []),
                title=act_row.title,
                normalized_title=act_row.normalized_title,
                act_type=act_row.act_type,
                status=act_row.status,
                procedure_type=act_row.procedure_type,
                amount_net=act_row.amount_net,
                amount_gross=act_row.amount_gross,
                currency=act_row.currency,
                cpv_codes=cpv_by_act.get(act_row.id, []),
                nuts_codes=nuts_by_act.get(act_row.id, []),
                buyer_id=buyer["id"] if buyer else None,
                buyer_name=buyer["name"] if buyer else None,
                supplier_ids=[party["id"] for party in act_suppliers],
                supplier_names=[party["name"] for party in act_suppliers],
                submission_date=act_row.submission_date,
                decision_date=act_row.decision_date,
            )
        )
    return documents


async def _load_act_for_indexing(conn: AsyncConnection, act_row) -> ActForIndexing:
    return (await _load_acts_for_indexing(conn, [act_row]))[0]


async def index_single_act(
    conn: AsyncConnection, http_client: httpx.AsyncClient, config: OpenSearchConfig, act_id: uuid.UUID
) -> None:
    act_row = (
        await conn.execute(
            select(procurement_acts).where(
                procurement_acts.c.id == act_id,
                func.procintel_act_is_analytics_eligible(procurement_acts.c.id),
            )
        )
    ).first()
    if act_row is None:
        return
    act = await _load_act_for_indexing(conn, act_row)
    await bulk_index(http_client, config, [build_act_document(act)])


async def reindex_all_acts(
    conn: AsyncConnection,
    http_client: httpx.AsyncClient,
    config: OpenSearchConfig,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ReindexResult:
    acts_indexed = 0
    last_id: uuid.UUID | None = None

    while True:
        query = (
            select(procurement_acts)
            .where(
                procurement_acts.c.is_current.is_(True),
                func.procintel_act_is_analytics_eligible(procurement_acts.c.id),
            )
            .order_by(procurement_acts.c.id)
        )
        if last_id is not None:
            query = query.where(procurement_acts.c.id > last_id)
        rows = (await conn.execute(query.limit(batch_size))).all()
        if not rows:
            break

        documents = [
            build_act_document(act)
            for act in await _load_acts_for_indexing(conn, list(rows))
        ]
        await bulk_index(http_client, config, documents)
        acts_indexed += len(documents)
        last_id = rows[-1].id

        if len(rows) < batch_size:
            break

    catalog_result = await reindex_catalogs(
        conn,
        http_client,
        config,
    )
    return ReindexResult(
        acts_indexed=acts_indexed,
        catalogs=catalog_result.counts,
    )


def _physical_build_config(
    config: OpenSearchConfig,
    build_id: str,
) -> OpenSearchConfig:
    suffix = build_id.casefold().replace("-", "_")
    return replace(
        config,
        index_name=f"{config.index_name}__{suffix}"[:240].rstrip("_"),
        index_prefix=f"{config.index_prefix}__{suffix}"[:200].rstrip("_"),
    )


async def rebuild_all_indexes_atomic(
    conn: AsyncConnection,
    http_client: httpx.AsyncClient,
    config: OpenSearchConfig,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    build_id: str | None = None,
) -> AtomicReindexResult:
    build_id = build_id or (
        datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        + "_"
        + uuid.uuid4().hex[:8]
    )
    physical = _physical_build_config(config, build_id)
    physical_names = {
        "procurement_acts": physical.index_name,
        **{
            catalog: physical.catalog_index_name(catalog)
            for catalog in CATALOGS
        },
    }
    logical_names = {
        "procurement_acts": config.index_name,
        **{
            catalog: config.catalog_index_name(catalog)
            for catalog in CATALOGS
        },
    }
    aliases = {
        logical_names[catalog]: physical_names[catalog]
        for catalog in physical_names
    }

    await create_index(http_client, physical, PROCUREMENT_ACTS_MAPPING)
    try:
        indexed = await reindex_all_acts(
            conn,
            http_client,
            physical,
            batch_size=batch_size,
        )
        expected = {
            "procurement_acts": indexed.acts_indexed,
            **(indexed.catalogs or {}),
        }
        for catalog, index_name in physical_names.items():
            target = replace(physical, index_name=index_name)
            await refresh_index(http_client, target)
            actual = await index_count(http_client, target)
            if actual != int(expected[catalog]):
                raise RuntimeError(
                    f"OpenSearch count mismatch for {catalog}: "
                    f"expected {expected[catalog]}, got {actual}"
                )

        old_indexes = await swap_index_aliases(http_client, config, aliases)
    except Exception:
        for index_name in physical_names.values():
            await delete_index(http_client, replace(physical, index_name=index_name))
        raise

    for index_name in old_indexes.difference(physical_names.values()):
        await delete_index(http_client, replace(config, index_name=index_name))

    return AtomicReindexResult(
        acts_indexed=indexed.acts_indexed,
        catalogs=indexed.catalogs or {},
        aliases=aliases,
        build_id=build_id,
    )

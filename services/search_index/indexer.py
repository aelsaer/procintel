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
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api.queries import load_identifiers, load_parties
from packages.domain.tables import act_cpv_codes, act_locations, procurement_acts

from .catalog import reindex_catalogs
from .client import bulk_index
from .config import OpenSearchConfig
from .document import ActForIndexing, build_act_document

DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class ReindexResult:
    acts_indexed: int
    catalogs: dict[str, int] | None = None


async def _load_act_for_indexing(conn: AsyncConnection, act_row) -> ActForIndexing:
    identifiers = await load_identifiers(conn, act_row.id)
    buyer, suppliers = await load_parties(conn, act_row.id)

    cpv_codes = (
        await conn.execute(select(act_cpv_codes.c.cpv_code).where(act_cpv_codes.c.act_id == act_row.id))
    ).scalars().all()
    nuts_codes = (
        await conn.execute(
            select(act_locations.c.nuts_code).where(
                act_locations.c.act_id == act_row.id, act_locations.c.nuts_code.is_not(None)
            )
        )
    ).scalars().all()

    return ActForIndexing(
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
        cpv_codes=list(cpv_codes),
        nuts_codes=list(nuts_codes),
        buyer_id=buyer["id"] if buyer else None,
        buyer_name=buyer["name"] if buyer else None,
        supplier_ids=[s["id"] for s in suppliers],
        supplier_names=[s["name"] for s in suppliers],
        submission_date=act_row.submission_date,
        decision_date=act_row.decision_date,
    )


async def index_single_act(
    conn: AsyncConnection, http_client: httpx.AsyncClient, config: OpenSearchConfig, act_id: uuid.UUID
) -> None:
    act_row = (await conn.execute(select(procurement_acts).where(procurement_acts.c.id == act_id))).one()
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
        query = select(procurement_acts).where(procurement_acts.c.is_current.is_(True)).order_by(procurement_acts.c.id)
        if last_id is not None:
            query = query.where(procurement_acts.c.id > last_id)
        rows = (await conn.execute(query.limit(batch_size))).all()
        if not rows:
            break

        documents = [build_act_document(await _load_act_for_indexing(conn, row)) for row in rows]
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

"""Create process-level enrichment work after adamChain resolution."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from services.ingestion.enrichment_queue import enqueue_enrichment


async def enqueue_process_diavgeia_search_jobs(
    conn: AsyncConnection,
    *,
    limit: int | None = None,
    process_ids: set[uuid.UUID] | None = None,
) -> int:
    """Queue one conservative Διαύγεια fallback search per ΚΗΜΔΗΣ process.

    Direct ΑΔΑ jobs are created during ingestion. This fallback runs only
    after adamChain has grouped acts, avoiding one fuzzy search for every
    lifecycle record in the same procurement.
    """

    limit_clause = "LIMIT :limit" if limit is not None else ""
    process_clause = (
        "AND p.id = ANY(CAST(:process_ids AS UUID[]))"
        if process_ids is not None
        else ""
    )
    parameters: dict[str, object] = {}
    if limit is not None:
        parameters["limit"] = limit
    if process_ids is not None:
        parameters["process_ids"] = list(process_ids)
    rows = (
        await conn.execute(
            sa.text(
                f"""
                SELECT p.id AS process_id,
                       representative.id AS act_id,
                       representative.title,
                       buyer.canonical_name AS buyer_name,
                       representative.source_record_id
                FROM procurement_processes p
                JOIN LATERAL (
                    SELECT a.id, a.title, a.source_record_id
                    FROM procurement_acts a
                    WHERE a.process_id = p.id
                      AND a.act_type IN ('NOTICE', 'AWARD', 'CONTRACT', 'REQUEST')
                      AND a.title IS NOT NULL
                    ORDER BY CASE a.act_type
                               WHEN 'NOTICE' THEN 1
                               WHEN 'AWARD' THEN 2
                               WHEN 'CONTRACT' THEN 3
                               ELSE 4
                             END,
                             COALESCE(
                               a.publication_date,
                               a.submission_date,
                               a.decision_date
                             ) DESC NULLS LAST
                    LIMIT 1
                ) representative ON TRUE
                JOIN act_parties buyer_party
                  ON buyer_party.act_id = representative.id
                 AND buyer_party.party_role IN ('BUYER', 'CONTRACTING_AUTHORITY')
                JOIN entities buyer ON buyer.id = buyer_party.entity_id
                WHERE p.record_status = 'ACTIVE'
                  {process_clause}
                  AND EXISTS (
                    SELECT 1
                    FROM procurement_acts khmdhs_act
                    JOIN act_identifiers adam
                      ON adam.act_id = khmdhs_act.id
                     AND adam.scheme = 'ADAM'
                    WHERE khmdhs_act.process_id = p.id
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM procurement_acts origin
                    JOIN act_links link ON link.to_act_id = origin.id
                    JOIN procurement_acts decision
                      ON decision.id = link.from_act_id
                     AND decision.act_type = 'DIAVGEIA_DECISION'
                    WHERE origin.process_id = p.id
                  )
                ORDER BY p.id
                {limit_clause}
                """
            ),
            parameters,
        )
    ).mappings().all()

    queued = 0
    seen_processes: set[uuid.UUID] = set()
    for row in rows:
        process_id = row["process_id"]
        if process_id in seen_processes:
            continue
        seen_processes.add(process_id)
        await enqueue_enrichment(
            conn,
            provider="DIAVGEIA_SEARCH",
            idempotency_key=str(process_id),
            payload={
                "origin_act_id": str(row["act_id"]),
                "organization_query": row["buyer_name"],
                "title_query": row["title"],
                "process_id": str(process_id),
            },
            object_type="procurement_process",
            object_id=process_id,
            source_record_id=row["source_record_id"],
            priority=120,
        )
        queued += 1
    await conn.commit()
    return queued

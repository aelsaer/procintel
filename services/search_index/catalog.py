"""Build the six non-act OpenSearch catalogs required by spec section 29."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import tenants

from .client import (
    bulk_index,
    create_index,
    delete_all_documents,
    index_exists,
)
from .config import OpenSearchConfig

CATALOGS = (
    "procurement_processes",
    "organizations",
    "companies",
    "funding_projects",
    "documents",
    "opportunities",
)

CATALOG_MAPPING: dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "filter": {
                "company_edge_ngram": {
                    "type": "edge_ngram",
                    "min_gram": 2,
                    "max_gram": 20,
                }
            },
            "analyzer": {
                "company_name_index": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": [
                        "lowercase",
                        "company_edge_ngram",
                    ],
                },
                "company_name_search": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase"],
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "process_id": {"type": "keyword"},
            "entity_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "public_id": {"type": "keyword"},
            "adam": {"type": "keyword"},
            "ada": {"type": "keyword"},
            "afm": {"type": "keyword"},
            "gemi": {"type": "keyword"},
            "mis": {"type": "keyword"},
            "ted_id": {"type": "keyword"},
            "aaht": {"type": "keyword"},
            "cpv_codes": {"type": "keyword"},
            "nuts_codes": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "greek",
                "fields": {
                    "raw": {"type": "keyword", "ignore_above": 512}
                },
            },
            "name": {
                "type": "text",
                "analyzer": "greek",
                "fields": {
                    "raw": {"type": "keyword", "ignore_above": 512},
                    "autocomplete": {
                        "type": "text",
                        "analyzer": "company_name_index",
                        "search_analyzer": "company_name_search",
                    },
                },
            },
            "description": {"type": "text", "analyzer": "greek"},
            "document_text": {"type": "text", "analyzer": "greek"},
            "status": {"type": "keyword"},
            "amount": {"type": "double"},
            "score": {"type": "double"},
            "event_date": {"type": "date"},
            "updated_at": {"type": "date"},
            "identifiers": {"type": "keyword"},
        }
    },
}


@dataclass(frozen=True)
class CatalogReindexResult:
    counts: dict[str, int]


def _config_for(
    config: OpenSearchConfig,
    catalog: str,
) -> OpenSearchConfig:
    return replace(config, index_name=config.catalog_index_name(catalog))


async def _ensure_index(
    client: httpx.AsyncClient,
    config: OpenSearchConfig,
    catalog: str,
) -> OpenSearchConfig:
    target = _config_for(config, catalog)
    if not await index_exists(client, target):
        await create_index(client, target, CATALOG_MAPPING)
    return target


async def _index_query(
    conn: AsyncConnection,
    client: httpx.AsyncClient,
    config: OpenSearchConfig,
    catalog: str,
    query: sa.TextClause,
    *,
    parameters: dict[str, Any] | None = None,
    batch_size: int = 500,
    reset: bool = True,
) -> int:
    target = await _ensure_index(client, config, catalog)
    if reset:
        await delete_all_documents(client, target)
    result = await conn.stream(query, parameters or {})
    count = 0
    batch: list[dict[str, Any]] = []
    async for row in result.mappings():
        batch.append(
            {
                key: _json_value(value)
                for key, value in row.items()
                if value is not None
            }
        )
        if len(batch) >= batch_size:
            await bulk_index(client, target, batch)
            count += len(batch)
            batch = []
    if batch:
        await bulk_index(client, target, batch)
        count += len(batch)
    return count


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


_PROCESS_QUERY = sa.text(
    """
    SELECT p.id::text AS id, 'procurement_process' AS kind,
           p.public_id, p.title, p.lifecycle_status AS status,
           ARRAY_REMOVE(ARRAY[p.primary_cpv_code], NULL) AS cpv_codes,
           COALESCE(
             p.current_contract_value, p.awarded_value, p.estimated_value
           )::double precision AS amount,
           buyer.canonical_name AS name,
           p.last_observed_at AS updated_at,
           ARRAY(
             SELECT DISTINCT ai.value_normalized
             FROM procurement_acts a
             JOIN act_identifiers ai ON ai.act_id=a.id
             WHERE a.process_id=p.id
           ) AS identifiers,
           ARRAY(
             SELECT DISTINCT l.nuts_code
             FROM procurement_acts a
             JOIN act_locations l ON l.act_id=a.id
             WHERE a.process_id=p.id AND l.nuts_code IS NOT NULL
           ) AS nuts_codes,
           ARRAY(
             SELECT DISTINCT ai.value_normalized
             FROM procurement_acts a
             JOIN act_identifiers ai ON ai.act_id=a.id
             WHERE a.process_id=p.id AND ai.scheme='ADAM'
           ) AS adam,
           ARRAY(
             SELECT DISTINCT ai.value_normalized
             FROM procurement_acts a
             JOIN act_identifiers ai ON ai.act_id=a.id
             WHERE a.process_id=p.id AND ai.scheme='ADA'
           ) AS ada
    FROM procurement_processes p
    LEFT JOIN entities buyer ON buyer.id=p.buyer_entity_id
    WHERE p.record_status='ACTIVE'
      AND EXISTS (
        SELECT 1
        FROM procurement_acts eligible_act
        WHERE eligible_act.process_id = p.id
          AND procintel_act_is_analytics_eligible(eligible_act.id)
      )
    ORDER BY p.id
    """
)

_ENTITY_QUERY = sa.text(
    """
    SELECT e.id::text AS id, :kind AS kind, e.id::text AS entity_id,
           e.canonical_name AS name, e.status, e.updated_at,
           ARRAY_AGG(DISTINCT i.scheme || ':' || i.value_normalized)
             FILTER (WHERE i.id IS NOT NULL) AS identifiers,
           ARRAY_AGG(DISTINCT i.value_normalized)
             FILTER (WHERE i.scheme='AFM') AS afm,
           ARRAY_AGG(DISTINCT i.value_normalized)
             FILTER (WHERE i.scheme IN ('GEMI','GEMI_NUMBER')) AS gemi
    FROM entities e
    LEFT JOIN entity_identifiers i ON i.entity_id=e.id AND i.is_current
    WHERE e.entity_type=:entity_type
    GROUP BY e.id
    ORDER BY e.id
    """
)

_FUNDING_QUERY = sa.text(
    """
    SELECT f.id::text AS id, 'funding_project' AS kind,
           f.mis_ops_code AS mis, f.title, f.description, f.status,
           f.budget::double precision AS amount,
           ARRAY_REMOVE(ARRAY[NULLIF(BTRIM(f.spatial), '')], NULL)
             AS nuts_codes,
           f.observed_at AS updated_at,
           ARRAY_REMOVE(ARRAY[f.mis_ops_code, f.program_code], NULL)
             AS identifiers
    FROM funding_projects f
    ORDER BY f.id
    """
)

_DOCUMENT_QUERY = sa.text(
    """
    SELECT d.id::text AS id, 'document' AS kind,
           d.id::text AS document_id, a.process_id::text AS process_id,
           d.title, d.document_type AS status, d.created_at AS updated_at,
           STRING_AGG(dp.text, E'\n' ORDER BY dp.page_number) AS document_text,
           ARRAY_REMOVE(ARRAY[d.sha256, d.source_url], NULL) AS identifiers
    FROM documents d
    LEFT JOIN procurement_acts a ON a.id=d.act_id
    LEFT JOIN document_pages dp ON dp.document_id=d.id
    GROUP BY d.id, a.process_id
    ORDER BY d.id
    """
)

_OPPORTUNITY_QUERY = sa.text(
    """
    SELECT (s.tenant_id::text || ':' || s.process_id::text) AS id,
           'opportunity' AS kind, s.tenant_id::text AS tenant_id,
           s.process_id::text AS process_id, p.public_id, p.title,
           p.lifecycle_status AS status,
           ARRAY_REMOVE(ARRAY[p.primary_cpv_code], NULL) AS cpv_codes,
           s.total_score::double precision AS score,
           COALESCE(
             p.current_contract_value, p.awarded_value, p.estimated_value
           )::double precision AS amount,
           s.computed_at AS updated_at
    FROM opportunity_scores s
    JOIN procurement_processes p ON p.id=s.process_id
    WHERE s.tenant_id=CAST(:tenant_id AS uuid)
    ORDER BY s.id
    """
)


async def reindex_catalogs(
    conn: AsyncConnection,
    client: httpx.AsyncClient,
    config: OpenSearchConfig,
) -> CatalogReindexResult:
    counts = {
        "procurement_processes": await _index_query(
            conn, client, config, "procurement_processes", _PROCESS_QUERY
        ),
        "organizations": await _index_query(
            conn,
            client,
            config,
            "organizations",
            _ENTITY_QUERY,
            parameters={
                "kind": "organization",
                "entity_type": "PUBLIC_ORGANIZATION",
            },
        ),
        "companies": await _index_query(
            conn,
            client,
            config,
            "companies",
            _ENTITY_QUERY,
            parameters={"kind": "company", "entity_type": "COMPANY"},
        ),
        "funding_projects": await _index_query(
            conn, client, config, "funding_projects", _FUNDING_QUERY
        ),
        "documents": await _index_query(
            conn,
            client,
            config,
            "documents",
            _DOCUMENT_QUERY,
            batch_size=50,
        ),
    }

    opportunity_count = 0
    tenant_ids = (await conn.execute(sa.select(tenants.c.id))).scalars().all()
    opportunity_target = await _ensure_index(
        client,
        config,
        "opportunities",
    )
    await delete_all_documents(client, opportunity_target)
    for tenant_id in tenant_ids:
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
            {"tenant_id": str(tenant_id)},
        )
        opportunity_count += await _index_query(
            conn,
            client,
            config,
            "opportunities",
            _OPPORTUNITY_QUERY,
            parameters={"tenant_id": str(tenant_id)},
            reset=False,
        )
    counts["opportunities"] = opportunity_count
    return CatalogReindexResult(counts=counts)

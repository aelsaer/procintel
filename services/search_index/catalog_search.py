"""Relevance search across canonical OpenSearch catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .client import search as opensearch_search
from .config import OpenSearchConfig

PUBLIC_CATALOGS = frozenset(
    {
        "procurement_processes",
        "organizations",
        "companies",
        "funding_projects",
        "documents",
    }
)


@dataclass(frozen=True)
class CatalogSearchHit:
    id: str
    catalog: str
    score: float
    source: dict[str, Any]


@dataclass(frozen=True)
class CatalogSearchResult:
    total: int
    hits: list[CatalogSearchHit]


def build_catalog_query(
    *,
    query: str,
    offset: int,
    limit: int,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = []
    if tenant_id is not None:
        filters.append({"term": {"tenant_id": tenant_id}})
    return {
        "query": {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": [
                                "title^5",
                                "name^5",
                                "description^2",
                                "document_text",
                            ],
                            "operator": "and",
                            "fuzziness": 0,
                        }
                    },
                    {
                        "multi_match": {
                            "query": query,
                            "fields": [
                                "identifiers^8",
                                "public_id^8",
                                "adam^8",
                                "ada^8",
                                "afm^8",
                                "gemi^8",
                                "mis^8",
                                "ted_id^8",
                            ],
                        }
                    },
                ],
                "minimum_should_match": 1,
                "filter": filters,
            }
        },
        "from": offset,
        "size": limit,
    }


async def search_catalogs(
    http_client: httpx.AsyncClient,
    config: OpenSearchConfig,
    *,
    query: str,
    catalogs: set[str],
    offset: int = 0,
    limit: int = 20,
    tenant_id: str | None = None,
) -> CatalogSearchResult:
    if not catalogs:
        return CatalogSearchResult(total=0, hits=[])
    index_names = ",".join(
        config.catalog_index_name(catalog)
        for catalog in sorted(catalogs)
    )
    target = OpenSearchConfig(
        base_url=config.base_url,
        index_name=index_names,
        index_prefix=config.index_prefix,
        username=config.username,
        password=config.password,
        request_timeout_seconds=config.request_timeout_seconds,
    )
    response = await opensearch_search(
        http_client,
        target,
        build_catalog_query(
            query=query,
            offset=offset,
            limit=limit,
            tenant_id=tenant_id,
        ),
    )
    hits_section = response.get("hits", {})
    total = hits_section.get("total", 0)
    total_value = total.get("value", 0) if isinstance(total, dict) else total
    hits = [
        CatalogSearchHit(
            id=str(hit.get("_id")),
            catalog=str(hit.get("_index", "")).removeprefix(
                f"{config.index_prefix}_"
            ),
            score=float(hit.get("_score") or 0.0),
            source=hit.get("_source") or {},
        )
        for hit in hits_section.get("hits", [])
    ]
    return CatalogSearchResult(total=int(total_value), hits=hits)

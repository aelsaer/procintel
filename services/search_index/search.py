"""Full-text search over the OpenSearch `procurement_acts` index — §29's
relevance-ranked full-text search, distinct from `apps/api/routers/search.py`'s
exact-identifier-first Postgres search (§29.4's ranking order still applies
there; this is the complementary "find contracts about X" relevance path
§11 explicitly names OpenSearch for).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .client import search as opensearch_search
from .config import OpenSearchConfig


@dataclass(frozen=True)
class FulltextSearchHit:
    id: str
    title: str | None
    act_type: str
    score: float
    buyer_name: str | None
    amount_gross: float | None
    cpv_codes: list[str]


@dataclass(frozen=True)
class FulltextSearchResult:
    total: int
    hits: list[FulltextSearchHit]


def build_query_body(
    *, query: str, cpv_prefix: str | None, nuts_code: str | None, offset: int, limit: int
) -> dict:
    must: list[dict] = [
        {
            "multi_match": {
                "query": query,
                "fields": ["title^3", "buyer_name^2", "supplier_names"],
                "operator": "and",
                "fuzziness": 0,
            }
        }
    ]
    filters: list[dict] = []
    if cpv_prefix:
        filters.append({"wildcard": {"cpv_codes": f"{cpv_prefix}*"}})
    if nuts_code:
        filters.append({"term": {"nuts_codes": nuts_code}})

    return {
        "query": {"bool": {"must": must, "filter": filters}},
        "from": offset,
        "size": limit,
    }


async def search_procurement_acts(
    http_client: httpx.AsyncClient,
    config: OpenSearchConfig,
    *,
    query: str,
    cpv_prefix: str | None = None,
    nuts_code: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> FulltextSearchResult:
    body = build_query_body(query=query, cpv_prefix=cpv_prefix, nuts_code=nuts_code, offset=offset, limit=limit)
    response = await opensearch_search(http_client, config, body)

    hits_section = response.get("hits", {})
    total = hits_section.get("total", {})
    total_value = total.get("value", 0) if isinstance(total, dict) else total

    hits = []
    for hit in hits_section.get("hits", []):
        source = hit.get("_source", {})
        hits.append(
            FulltextSearchHit(
                id=hit.get("_id"),
                title=source.get("title"),
                act_type=source.get("act_type"),
                score=hit.get("_score") or 0.0,
                buyer_name=source.get("buyer_name"),
                amount_gross=source.get("amount_gross"),
                cpv_codes=source.get("cpv_codes", []),
            )
        )
    return FulltextSearchResult(total=total_value, hits=hits)

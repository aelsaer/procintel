"""GET /v1/search/fulltext — OpenSearch-backed relevance search
(description.txt §11/§29's "OpenSearch για full-text"), complementary to
`routers/search.py`'s exact-identifier-first Postgres search.

Kept as a separate endpoint rather than folded into `/v1/search` itself:
`/v1/search` is real, tested, and already implements §29.4's exact-match-
first ranking correctly against Postgres — swapping its ranking logic for
OpenSearch mid-flight risks that correctness for a backend this sandbox
can't confirm is even reachable. This endpoint is the OpenSearch-specific
one; unauthenticated, same "shared public data" posture as every other
procurement-data router (§38).
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from services.search_index.config import OpenSearchConfig
from services.search_index.search import search_procurement_acts

from ..deps import get_http_client

router = APIRouter(prefix="/v1/search", tags=["search"])


class FulltextHitResponse(BaseModel):
    id: str
    title: str | None
    act_type: str
    score: float
    buyer_name: str | None
    amount_gross: float | None
    cpv_codes: list[str]


class FulltextSearchResponse(BaseModel):
    total: int
    data: list[FulltextHitResponse]


@router.get("/fulltext", response_model=FulltextSearchResponse)
async def search_fulltext(
    q: str = Query(..., min_length=1),
    cpv_prefix: str | None = Query(default=None),
    nuts_code: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, le=100, gt=0),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> FulltextSearchResponse:
    config = OpenSearchConfig.from_env()
    result = await search_procurement_acts(
        http_client, config, query=q, cpv_prefix=cpv_prefix, nuts_code=nuts_code, offset=offset, limit=limit
    )
    return FulltextSearchResponse(
        total=result.total,
        data=[
            FulltextHitResponse(
                id=hit.id,
                title=hit.title,
                act_type=hit.act_type,
                score=hit.score,
                buyer_name=hit.buyer_name,
                amount_gross=hit.amount_gross,
                cpv_codes=hit.cpv_codes,
            )
            for hit in result.hits
        ],
    )

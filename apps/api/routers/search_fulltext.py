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

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from packages.auth.jwt_verifier import AuthenticatedUser
from services.search_index.catalog_search import (
    PUBLIC_CATALOGS,
    search_catalogs,
)
from services.search_index.config import OpenSearchConfig
from services.search_index.search import search_procurement_acts

from ..auth import get_current_user
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


class CatalogHitResponse(BaseModel):
    id: str
    catalog: str
    score: float
    source: dict[str, Any]


class CatalogSearchResponse(BaseModel):
    total: int
    data: list[CatalogHitResponse]


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


@router.get("/catalogs", response_model=CatalogSearchResponse)
async def search_public_catalogs(
    q: str = Query(..., min_length=1),
    catalog: list[str] | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, le=100, gt=0),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> CatalogSearchResponse:
    requested = set(catalog or PUBLIC_CATALOGS)
    unknown = requested - PUBLIC_CATALOGS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported public catalogs: {', '.join(sorted(unknown))}",
        )
    result = await search_catalogs(
        http_client,
        OpenSearchConfig.from_env(),
        query=q,
        catalogs=requested,
        offset=offset,
        limit=limit,
    )
    return CatalogSearchResponse(
        total=result.total,
        data=[
            CatalogHitResponse(
                id=hit.id,
                catalog=hit.catalog,
                score=hit.score,
                source=hit.source,
            )
            for hit in result.hits
        ],
    )


@router.get(
    "/catalogs/opportunities",
    response_model=CatalogSearchResponse,
)
async def search_tenant_opportunities(
    q: str = Query(..., min_length=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, le=100, gt=0),
    user: AuthenticatedUser = Depends(get_current_user),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> CatalogSearchResponse:
    if not user.tenant_id:
        raise HTTPException(
            status_code=400,
            detail="token carries no tenant_id claim",
        )
    result = await search_catalogs(
        http_client,
        OpenSearchConfig.from_env(),
        query=q,
        catalogs={"opportunities"},
        offset=offset,
        limit=limit,
        tenant_id=user.tenant_id,
    )
    return CatalogSearchResponse(
        total=result.total,
        data=[
            CatalogHitResponse(
                id=hit.id,
                catalog=hit.catalog,
                score=hit.score,
                source=hit.source,
            )
            for hit in result.hits
        ],
    )

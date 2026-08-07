"""Exact-identifier and strict lexical search over the loaded archive."""

from __future__ import annotations

import base64
import json
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.schemas.responses import PaginationBlock, SearchResponse, SearchResultItem
from services.ingestion.on_demand import classify_identifier, ensure_fetch_request
from services.intelligence.tender_brief import links_for_display_identifier
from services.search_index.lexical import (
    normalize_lexical_text,
    normalized_text_sql,
    query_prefilter,
    query_token_patterns,
)

from ..db import get_conn
from .fetch_requests import build_fetch_request_response, load_fetch_request, schedule_fetch_request

router = APIRouter(prefix="/v1/search", tags=["search"])

DEFAULT_LIMIT = 20
_TITLE_SEARCH_SQL = normalized_text_sql("a.title")


def _encode_cursor(offset: int) -> str:
    payload = json.dumps({"o": offset})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return max(int(payload["o"]), 0)
    except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return 0


@router.get("", response_model=SearchResponse)
async def search(
    background_tasks: BackgroundTasks,
    q: str = Query(..., min_length=1, max_length=200),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, le=100, gt=0),
    auto_fetch: bool = Query(default=False),
    act_type: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    amount_min: Decimal | None = Query(default=None, ge=0),
    amount_max: Decimal | None = Query(default=None, ge=0),
    cpv_prefix: str | None = Query(default=None, max_length=12),
    nuts_code: str | None = Query(default=None, max_length=8),
    municipality: str | None = Query(default=None, max_length=160),
    conn: AsyncConnection = Depends(get_conn),
) -> SearchResponse:
    raw_query = q.strip()
    normalized_query = normalize_lexical_text(raw_query)
    query_upper = raw_query.upper()
    token_patterns = query_token_patterns(raw_query)
    lexical_prefilter = query_prefilter(raw_query)
    phrase = f"% {normalized_query} %" if normalized_query else ""
    offset = _decode_cursor(cursor)
    cpv_like = f"{cpv_prefix.strip().split('-', 1)[0]}%" if cpv_prefix and cpv_prefix.strip() else None

    sql = sa.text(
        f"""
        WITH candidate_matches AS (
            SELECT identifier.act_id AS id, 100 AS relevance_rank
            FROM act_identifiers identifier
            WHERE identifier.scheme IN ('ADAM', 'ADA')
              AND identifier.value_normalized = :query_upper

            UNION ALL

            SELECT DISTINCT party.act_id AS id, 90 AS relevance_rank
            FROM entity_identifiers entity_identifier
            JOIN act_parties party ON party.entity_id = entity_identifier.entity_id
            WHERE CAST(:is_afm AS BOOLEAN)
              AND entity_identifier.scheme = 'AFM'
              AND entity_identifier.is_current = TRUE
              AND entity_identifier.value_normalized = :query_upper

            UNION ALL

            SELECT
                a.id,
                CASE
                    WHEN (' ' || {_TITLE_SEARCH_SQL} || ' ') LIKE :phrase THEN 80
                    ELSE 70
                END AS relevance_rank
            FROM procurement_acts a
            WHERE CAST(:has_lexical AS BOOLEAN)
              AND {_TITLE_SEARCH_SQL} ILIKE :lexical_prefilter
              AND {_TITLE_SEARCH_SQL} ~* ALL(CAST(:token_patterns AS TEXT[]))
        ),
        deduplicated_matches AS (
            SELECT id, MAX(relevance_rank) AS relevance_rank
            FROM candidate_matches
            GROUP BY id
        ),
        ranked AS (
            SELECT
                a.id,
                a.process_id,
                a.title,
                a.act_type,
                COALESCE(a.publication_date, a.submission_date, a.decision_date) AS event_date,
                COALESCE(
                    COALESCE(a.publication_date, a.submission_date, a.decision_date),
                    DATE '0001-01-01'
                ) AS sort_date,
                deduplicated_matches.relevance_rank,
                CASE deduplicated_matches.relevance_rank
                    WHEN 100 THEN 'EXACT_IDENTIFIER'
                    WHEN 90 THEN 'EXACT_ENTITY_IDENTIFIER'
                    WHEN 80 THEN 'EXACT_PHRASE'
                    ELSE 'TITLE_TERMS'
                END AS match_type
            FROM deduplicated_matches
            JOIN procurement_acts a ON a.id = deduplicated_matches.id
            WHERE procintel_act_is_analytics_eligible(a.id)
              AND (CAST(:act_type AS TEXT) IS NULL OR a.act_type = CAST(:act_type AS TEXT))
              AND (
                  CAST(:date_from AS DATE) IS NULL
                  OR COALESCE(a.publication_date, a.submission_date, a.decision_date) >= CAST(:date_from AS DATE)
              )
              AND (
                  CAST(:date_to AS DATE) IS NULL
                  OR COALESCE(a.publication_date, a.submission_date, a.decision_date) <= CAST(:date_to AS DATE)
              )
              AND (
                  CAST(:amount_min AS NUMERIC) IS NULL
                  OR COALESCE(a.amount_gross, a.amount_net, 0) >= CAST(:amount_min AS NUMERIC)
              )
              AND (
                  CAST(:amount_max AS NUMERIC) IS NULL
                  OR COALESCE(a.amount_gross, a.amount_net, 0) <= CAST(:amount_max AS NUMERIC)
              )
              AND (
                  CAST(:cpv_like AS TEXT) IS NULL
                  OR EXISTS (
                      SELECT 1 FROM act_cpv_codes filter_cpv
                      WHERE filter_cpv.act_id = a.id
                        AND filter_cpv.cpv_code LIKE CAST(:cpv_like AS TEXT)
                  )
              )
              AND (
                  CAST(:nuts_like AS TEXT) IS NULL
                  OR EXISTS (
                      SELECT 1 FROM act_locations location_filter
                      WHERE location_filter.act_id = a.id
                        AND UPPER(location_filter.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                  )
              )
              AND (
                  CAST(:municipality_like AS TEXT) IS NULL
                  OR EXISTS (
                      SELECT 1 FROM act_locations municipality_filter
                      WHERE municipality_filter.act_id = a.id
                        AND (
                            municipality_filter.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                            OR municipality_filter.place_text ILIKE CAST(:municipality_like AS TEXT)
                            OR municipality_filter.regional_unit_name ILIKE CAST(:municipality_like AS TEXT)
                        )
                  )
              )
        ),
        result_page AS (
            SELECT *
            FROM ranked
            ORDER BY relevance_rank DESC, sort_date DESC, id DESC
            OFFSET :offset
            LIMIT :fetch_limit
        )
        SELECT
            result_page.*,
            display_identifier.scheme AS identifier_scheme,
            display_identifier.value_normalized AS identifier_value,
            buyer.canonical_name AS buyer_name,
            COALESCE(cpv.cpv_codes, ARRAY[]::TEXT[]) AS cpv_codes
        FROM result_page
        LEFT JOIN LATERAL (
            SELECT identifier.scheme, identifier.value_normalized
            FROM act_identifiers identifier
            WHERE identifier.act_id = result_page.id
              AND identifier.scheme IN ('ADAM', 'ADA')
            ORDER BY
                (identifier.value_normalized = :query_upper) DESC,
                (identifier.scheme = 'ADAM') DESC,
                identifier.value_normalized
            LIMIT 1
        ) display_identifier ON TRUE
        LEFT JOIN LATERAL (
            SELECT entity.canonical_name
            FROM act_parties party
            JOIN entities entity ON entity.id = party.entity_id
            WHERE party.act_id = result_page.id
              AND party.party_role IN ('BUYER', 'CONTRACTING_AUTHORITY')
            ORDER BY (party.party_role = 'BUYER') DESC, entity.canonical_name
            LIMIT 1
        ) buyer ON TRUE
        LEFT JOIN LATERAL (
            SELECT ARRAY_AGG(code.cpv_code ORDER BY code.is_primary DESC, code.cpv_code) AS cpv_codes
            FROM act_cpv_codes code
            WHERE code.act_id = result_page.id
        ) cpv ON TRUE
        ORDER BY result_page.relevance_rank DESC, result_page.sort_date DESC, result_page.id DESC
        """
    )
    params = {
        "query_upper": query_upper,
        "is_afm": raw_query.isdigit() and len(raw_query) == 9,
        "phrase": phrase,
        "has_lexical": bool(token_patterns),
        "lexical_prefilter": lexical_prefilter,
        "token_patterns": token_patterns,
        "act_type": act_type.upper() if act_type else None,
        "date_from": date_from,
        "date_to": date_to,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "cpv_like": cpv_like,
        "nuts_like": f"{nuts_code.strip().upper()}%" if nuts_code and nuts_code.strip() else None,
        "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
        "offset": offset,
        "fetch_limit": limit + 1,
    }
    rows = (await conn.execute(sql, params)).all()
    has_more = len(rows) > limit
    usable_rows = rows[:limit]

    results = []
    for row in usable_rows:
        official_url, document_url = links_for_display_identifier(
            row.identifier_scheme,
            row.identifier_value,
        )
        results.append(SearchResultItem(
            act_id=str(row.id),
            process_id=str(row.process_id) if row.process_id else None,
            adam=row.identifier_value if row.identifier_scheme == "ADAM" else None,
            identifier_scheme=row.identifier_scheme,
            identifier_value=row.identifier_value,
            title=row.title,
            act_type=row.act_type,
            match_type=row.match_type,
            relevance=float(row.relevance_rank),
            buyer_name=row.buyer_name,
            cpv_codes=list(row.cpv_codes or []),
            event_date=row.event_date,
            official_url=official_url,
            document_url=document_url,
        ))

    next_cursor = None
    if has_more and usable_rows:
        next_cursor = _encode_cursor(offset + len(usable_rows))

    fetch_request_response = None
    if auto_fetch and cursor is None and not results and classify_identifier(q) is not None:
        fetch_request_id = await ensure_fetch_request(conn, q)
        if fetch_request_id is not None:
            fetch_request_row = await load_fetch_request(conn, fetch_request_id)
            if fetch_request_row.status == "QUEUED":
                schedule_fetch_request(background_tasks, fetch_request_id)
            fetch_request_response = build_fetch_request_response(fetch_request_row)

    return SearchResponse(
        data=results,
        pagination=PaginationBlock(next_cursor=next_cursor, has_more=has_more),
        fetch_request=fetch_request_response,
    )

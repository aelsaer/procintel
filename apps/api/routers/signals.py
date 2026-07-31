"""Tenant-profile-scoped early procurement demand signals."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import business_profiles, entities, procurement_signals

from ..auth import get_current_user
from ..db import get_tenant_scoped_conn
from ..workspace import tenant_uuid

router = APIRouter(prefix="/v1/intelligence/signals", tags=["intelligence"])


class ProcurementSignalResponse(BaseModel):
    id: str
    signal_type: str
    title: str
    description: str | None
    buyer_name: str | None
    source_url: str | None
    source_identifier: str | None
    publication_date: date | None
    expected_notice_date: date | None
    estimated_value: Decimal | None
    currency: str
    cpv_codes: list[str]
    nuts_codes: list[str]
    confidence: float
    evidence: dict
    process_id: str | None


@router.get("", response_model=list[ProcurementSignalResponse])
async def list_procurement_signals(
    signal_type: str | None = None,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    nuts_code: str | None = Query(default=None, max_length=8),
    municipality: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=50, ge=1, le=200),
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[ProcurementSignalResponse]:
    profile = (
        await conn.execute(
            sa.select(business_profiles).where(business_profiles.c.tenant_id == tenant_uuid(user))
        )
    ).first()
    cpv_likes = [f"{value.strip()}%" for value in (profile.cpv_prefixes if profile else []) if value.strip()]
    keyword_likes = [f"%{value.strip()}%" for value in (profile.keywords if profile else []) if value.strip()]
    excluded_cpv_likes = [
        f"{value.strip()}%" for value in (profile.excluded_cpv_prefixes if profile else []) if value.strip()
    ]
    excluded_keyword_likes = [
        f"%{value.strip()}%" for value in (profile.excluded_keywords if profile else []) if value.strip()
    ]
    requested_nuts = [nuts_code] if nuts_code and nuts_code.strip() else (profile.nuts_codes if profile else [])
    nuts_likes = [f"{value.strip().upper()}%" for value in requested_nuts if value.strip()]
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT s.*, buyer.canonical_name AS buyer_name
                FROM procurement_signals s
                LEFT JOIN entities buyer ON buyer.id = s.buyer_entity_id
                WHERE s.is_current
                  AND (CAST(:signal_type AS text) IS NULL OR s.signal_type = CAST(:signal_type AS text))
                  AND (
                      CAST(:date_from AS DATE) IS NULL
                      OR COALESCE(s.expected_notice_date, s.publication_date) >= CAST(:date_from AS DATE)
                  )
                  AND (
                      CAST(:date_to AS DATE) IS NULL
                      OR COALESCE(s.expected_notice_date, s.publication_date) <= CAST(:date_to AS DATE)
                  )
                  AND (
                      CARDINALITY(CAST(:cpv_likes AS text[])) = 0
                      AND CARDINALITY(CAST(:keyword_likes AS text[])) = 0
                      OR EXISTS (
                          SELECT 1 FROM unnest(s.cpv_codes) code
                          WHERE code LIKE ANY(CAST(:cpv_likes AS text[]))
                      )
                      OR s.title ILIKE ANY(CAST(:keyword_likes AS text[]))
                      OR COALESCE(s.description, '') ILIKE ANY(CAST(:keyword_likes AS text[]))
                  )
                  AND (
                      CARDINALITY(CAST(:nuts_likes AS text[])) = 0
                      OR EXISTS (
                          SELECT 1 FROM unnest(s.nuts_codes) code
                          WHERE code LIKE ANY(CAST(:nuts_likes AS text[]))
                      )
                  )
                  AND (
                      CAST(:municipality_like AS TEXT) IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM procurement_acts linked_act
                          JOIN act_locations linked_location ON linked_location.act_id = linked_act.id
                          WHERE linked_act.process_id = s.linked_process_id
                            AND (
                                linked_location.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                OR linked_location.place_text ILIKE CAST(:municipality_like AS TEXT)
                                OR linked_location.regional_unit_name ILIKE CAST(:municipality_like AS TEXT)
                            )
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM unnest(s.cpv_codes) code
                      WHERE code LIKE ANY(CAST(:excluded_cpv_likes AS text[]))
                  )
                  AND NOT (
                      s.title ILIKE ANY(CAST(:excluded_keyword_likes AS text[]))
                      OR COALESCE(s.description, '') ILIKE ANY(CAST(:excluded_keyword_likes AS text[]))
                  )
                ORDER BY s.expected_notice_date ASC NULLS LAST,
                         s.publication_date DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {
                "signal_type": signal_type.upper() if signal_type else None,
                "date_from": date_from,
                "date_to": date_to,
                "cpv_likes": cpv_likes,
                "keyword_likes": keyword_likes,
                "excluded_cpv_likes": excluded_cpv_likes,
                "excluded_keyword_likes": excluded_keyword_likes,
                "nuts_likes": nuts_likes,
                "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
                "limit": limit,
            },
        )
    ).mappings().all()
    return [
        ProcurementSignalResponse(
            id=str(row["id"]),
            signal_type=row["signal_type"],
            title=row["title"],
            description=row["description"],
            buyer_name=row["buyer_name"],
            source_url=row["source_url"],
            source_identifier=row["source_identifier"],
            publication_date=row["publication_date"],
            expected_notice_date=row["expected_notice_date"],
            estimated_value=row["estimated_value"],
            currency=row["currency"],
            cpv_codes=row["cpv_codes"] or [],
            nuts_codes=row["nuts_codes"] or [],
            confidence=float(row["confidence"]),
            evidence=row["evidence"] or {},
            process_id=str(row["linked_process_id"]) if row["linked_process_id"] else None,
        )
        for row in rows
    ]

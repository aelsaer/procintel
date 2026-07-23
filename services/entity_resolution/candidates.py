"""Explainable fuzzy entity candidate generation for the review queue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import entity_addresses, entity_identifiers, entity_match_candidates


@dataclass(frozen=True)
class CandidateGenerationResult:
    pairs_considered: int
    candidates_written: int
    identifier_conflicts: int


async def generate_match_candidates(
    conn: AsyncConnection,
    *,
    minimum_name_similarity: float = 0.58,
    limit: int = 1000,
) -> CandidateGenerationResult:
    rows = (await conn.execute(sa.text(
        """
        WITH entity_afm AS (
            SELECT entity_id, ARRAY_AGG(DISTINCT value_normalized) AS afms
            FROM entity_identifiers
            WHERE scheme = 'AFM' AND is_current = TRUE AND identifier_valid = TRUE
            GROUP BY entity_id
        ), entity_address AS (
            SELECT DISTINCT ON (entity_id) entity_id, municipality, postal_code
            FROM entity_addresses
            WHERE is_current = TRUE
            ORDER BY entity_id, valid_from DESC NULLS LAST
        )
        SELECT
            a.id AS entity_a_id, b.id AS entity_b_id,
            a.canonical_name AS name_a, b.canonical_name AS name_b,
            a.entity_type, similarity(a.normalized_name, b.normalized_name) AS name_similarity,
            aa.municipality AS municipality_a, ba.municipality AS municipality_b,
            aa.postal_code AS postal_a, ba.postal_code AS postal_b,
            COALESCE(ai.afms, '{}') AS afms_a, COALESCE(bi.afms, '{}') AS afms_b
        FROM entities a
        JOIN entities b ON b.id > a.id AND b.entity_type = a.entity_type
        LEFT JOIN entity_afm ai ON ai.entity_id = a.id
        LEFT JOIN entity_afm bi ON bi.entity_id = b.id
        LEFT JOIN entity_address aa ON aa.entity_id = a.id
        LEFT JOIN entity_address ba ON ba.entity_id = b.id
        WHERE a.status = 'ACTIVE' AND b.status = 'ACTIVE'
          AND a.entity_type IN ('COMPANY', 'PUBLIC_ORGANIZATION', 'CONSORTIUM')
          AND similarity(a.normalized_name, b.normalized_name) >= :minimum_similarity
        ORDER BY name_similarity DESC, a.id, b.id
        LIMIT :limit
        """
    ), {"minimum_similarity": minimum_name_similarity, "limit": limit})).all()

    written = 0
    conflicts = 0
    for row in rows:
        afms_a, afms_b = set(row.afms_a or []), set(row.afms_b or [])
        identifier_conflict = bool(afms_a and afms_b and afms_a.isdisjoint(afms_b))
        municipality_match = bool(row.municipality_a and row.municipality_b and row.municipality_a.casefold() == row.municipality_b.casefold())
        postal_match = bool(row.postal_a and row.postal_b and row.postal_a == row.postal_b)
        score = row.name_similarity * 0.75 + (0.15 if municipality_match else 0) + (0.10 if postal_match else 0)
        if identifier_conflict:
            score = min(score, 0.69)
            conflicts += 1
        breakdown = {
            "name_similarity": round(float(row.name_similarity), 4),
            "municipality_match": municipality_match,
            "postal_code_match": postal_match,
            "identifier_conflict": identifier_conflict,
            "afm_a": sorted(afms_a),
            "afm_b": sorted(afms_b),
            "suggested_action": "REJECT_CONFLICT" if identifier_conflict else ("REVIEW_HIGH" if score >= 0.85 else "REVIEW"),
        }
        result = await conn.execute(
            pg_insert(entity_match_candidates)
            .values(
                id=uuid.uuid4(), entity_a_id=row.entity_a_id, entity_b_id=row.entity_b_id,
                score=Decimal(str(round(score, 4))), score_breakdown=breakdown,
                blocking_reason="SAME_TYPE_TRIGRAM_NAME",
            )
            .on_conflict_do_update(
                index_elements=[entity_match_candidates.c.entity_a_id, entity_match_candidates.c.entity_b_id],
                set_={
                    "score": Decimal(str(round(score, 4))), "score_breakdown": breakdown,
                    "blocking_reason": "SAME_TYPE_TRIGRAM_NAME",
                },
                where=entity_match_candidates.c.status == "PENDING_REVIEW",
            )
        )
        if result.rowcount:
            written += 1
    return CandidateGenerationResult(len(rows), written, conflicts)

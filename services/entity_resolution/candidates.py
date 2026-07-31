"""Explainable multi-source entity candidate generation for the review queue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import entity_match_candidates


@dataclass(frozen=True)
class CandidateGenerationResult:
    pairs_considered: int
    candidates_written: int
    identifier_conflicts: int


def _values(value: Any) -> set[str]:
    return {str(item) for item in (value or []) if item}


def score_candidate(row: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    """Apply the published field weights and return the evidence breakdown."""
    afms_a, afms_b = _values(row.get("afms_a")), _values(row.get("afms_b"))
    domains_a, domains_b = _values(row.get("domains_a")), _values(row.get("domains_b"))
    phones_a, phones_b = _values(row.get("phones_a")), _values(row.get("phones_b"))
    emails_a, emails_b = _values(row.get("emails_a")), _values(row.get("emails_b"))
    identifier_conflict = bool(afms_a and afms_b and afms_a.isdisjoint(afms_b))
    domain_match = bool(domains_a & domains_b)
    phone_match = bool(phones_a & phones_b)
    email_match = bool(emails_a & emails_b)
    municipality_match = bool(
        row.get("municipality_a")
        and row.get("municipality_b")
        and str(row["municipality_a"]).casefold() == str(row["municipality_b"]).casefold()
    )
    postal_match = bool(
        row.get("postal_a")
        and row.get("postal_b")
        and str(row["postal_a"]).replace(" ", "") == str(row["postal_b"]).replace(" ", "")
    )
    temporal_compatibility = bool(row.get("temporal_compatibility", True))
    name_similarity = max(0.0, min(1.0, float(row.get("name_similarity") or 0)))
    address_similarity = max(0.0, min(1.0, float(row.get("address_similarity") or 0)))
    source_reliability = max(0.0, min(1.0, float(row.get("source_reliability") or 0.7)))
    score = (
        name_similarity * 0.40
        + address_similarity * 0.15
        + (0.06 if municipality_match else 0)
        + (0.06 if postal_match else 0)
        + (0.12 if domain_match else 0)
        + (0.10 if phone_match else 0)
        + (0.05 if email_match else 0)
        + (0.03 if temporal_compatibility else 0)
        + source_reliability * 0.03
    )
    if identifier_conflict:
        score = min(score, 0.69)
    score = round(min(score, 1.0), 4)
    breakdown = {
        "name_similarity": round(name_similarity, 4),
        "address_similarity": round(address_similarity, 4),
        "municipality_match": municipality_match,
        "postal_code_match": postal_match,
        "domain_match": domain_match,
        "phone_match": phone_match,
        "email_match": email_match,
        "temporal_compatibility": temporal_compatibility,
        "source_reliability": round(source_reliability, 4),
        "identifier_conflict": identifier_conflict,
        "afm_a": sorted(afms_a),
        "afm_b": sorted(afms_b),
        "matching_domains": sorted(domains_a & domains_b),
        "matching_phones": sorted(phones_a & phones_b),
        "matching_emails": sorted(emails_a & emails_b),
        "suggested_action": (
            "REJECT_CONFLICT"
            if identifier_conflict
            else "REVIEW_HIGH"
            if score >= 0.85
            else "REVIEW"
        ),
    }
    return score, breakdown


async def generate_match_candidates(
    conn: AsyncConnection,
    *,
    minimum_name_similarity: float = 0.58,
    limit: int = 1000,
) -> CandidateGenerationResult:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH source_reliability AS (
                    SELECT id,
                           CASE source_system
                               WHEN 'GEMI' THEN 1.00
                               WHEN 'KHMDHS' THEN 0.97
                               WHEN 'DIAVGEIA' THEN 0.94
                               WHEN 'VIES' THEN 0.93
                               WHEN 'TED' THEN 0.88
                               WHEN 'ANAPTYXI' THEN 0.88
                               WHEN 'CKAN' THEN 0.82
                               ELSE 0.70
                           END AS reliability
                    FROM source_records
                ), entity_afm AS (
                    SELECT entity_id, ARRAY_AGG(DISTINCT value_normalized) AS afms
                    FROM entity_identifiers
                    WHERE scheme = 'AFM' AND is_current = TRUE AND identifier_valid = TRUE
                    GROUP BY entity_id
                ), entity_address AS (
                    SELECT DISTINCT ON (a.entity_id)
                           a.entity_id, a.address_line, a.municipality, a.postal_code,
                           concat_ws(' ', a.address_line, a.postal_code, a.municipality, a.region) AS full_address,
                           a.valid_from, a.valid_to,
                           COALESCE(sr.reliability, 0.70) AS reliability
                    FROM entity_addresses a
                    LEFT JOIN source_reliability sr ON sr.id = a.source_record_id
                    ORDER BY a.entity_id, a.is_current DESC, a.valid_from DESC NULLS LAST
                ), entity_contact AS (
                    SELECT c.entity_id,
                           ARRAY_AGG(DISTINCT c.value_normalized) FILTER (WHERE c.contact_type = 'DOMAIN') AS domains,
                           ARRAY_AGG(DISTINCT c.value_normalized) FILTER (WHERE c.contact_type = 'PHONE') AS phones,
                           ARRAY_AGG(DISTINCT c.value_normalized) FILTER (WHERE c.contact_type = 'EMAIL') AS emails,
                           MIN(c.valid_from) AS valid_from,
                           MAX(c.valid_to) FILTER (WHERE c.valid_to IS NOT NULL) AS valid_to,
                           MAX(COALESCE(sr.reliability, 0.70)) AS reliability
                    FROM entity_contacts c
                    LEFT JOIN source_reliability sr ON sr.id = c.source_record_id
                    WHERE c.is_current = TRUE
                    GROUP BY c.entity_id
                ), entity_name_reliability AS (
                    SELECT n.entity_id, MAX(COALESCE(sr.reliability, 0.70)) AS reliability
                    FROM entity_names n
                    LEFT JOIN source_reliability sr ON sr.id = n.source_record_id
                    WHERE n.is_current = TRUE
                    GROUP BY n.entity_id
                ), candidate_pairs AS MATERIALIZED (
                    SELECT entity_a_id, entity_b_id
                    FROM (
                        SELECT a.id AS entity_a_id, b.id AS entity_b_id,
                               similarity(a.normalized_name, b.normalized_name) AS name_similarity
                        FROM entities a
                        JOIN entities b
                          ON b.id > a.id
                         AND b.entity_type = a.entity_type
                         AND b.status = 'ACTIVE'
                         AND b.normalized_name % a.normalized_name
                        WHERE a.status = 'ACTIVE'
                          AND a.entity_type IN ('COMPANY', 'PUBLIC_ORGANIZATION', 'CONSORTIUM')
                          AND similarity(a.normalized_name, b.normalized_name) >= :minimum_similarity

                        UNION ALL

                        SELECT a.id, b.id,
                               similarity(a.normalized_name, b.normalized_name)
                        FROM entity_contacts ac
                        JOIN entity_contacts bc
                          ON bc.entity_id > ac.entity_id
                         AND bc.contact_type = ac.contact_type
                         AND bc.value_normalized = ac.value_normalized
                         AND bc.is_current = TRUE
                        JOIN entities a ON a.id = ac.entity_id
                        JOIN entities b ON b.id = bc.entity_id
                        WHERE ac.is_current = TRUE
                          AND ac.contact_type IN ('DOMAIN', 'PHONE', 'EMAIL')
                          AND NULLIF(ac.value_normalized, '') IS NOT NULL
                          AND a.status = 'ACTIVE'
                          AND b.status = 'ACTIVE'
                          AND a.entity_type = b.entity_type

                        UNION ALL

                        SELECT a.id, b.id,
                               similarity(a.normalized_name, b.normalized_name)
                        FROM entity_addresses aa
                        JOIN entity_addresses ba
                          ON ba.entity_id > aa.entity_id
                         AND REPLACE(ba.postal_code, ' ', '') = REPLACE(aa.postal_code, ' ', '')
                         AND ba.is_current = TRUE
                        JOIN entities a ON a.id = aa.entity_id
                        JOIN entities b ON b.id = ba.entity_id
                        WHERE aa.is_current = TRUE
                          AND NULLIF(REPLACE(aa.postal_code, ' ', ''), '') IS NOT NULL
                          AND a.status = 'ACTIVE'
                          AND b.status = 'ACTIVE'
                          AND a.entity_type = b.entity_type
                          AND similarity(a.normalized_name, b.normalized_name) >= 0.40
                    ) blocked
                    GROUP BY entity_a_id, entity_b_id
                    ORDER BY MAX(name_similarity) DESC, entity_a_id, entity_b_id
                    LIMIT :limit
                )
                SELECT
                    a.id AS entity_a_id, b.id AS entity_b_id,
                    a.canonical_name AS name_a, b.canonical_name AS name_b,
                    a.entity_type,
                    similarity(a.normalized_name, b.normalized_name) AS name_similarity,
                    similarity(COALESCE(aa.full_address, ''), COALESCE(ba.full_address, '')) AS address_similarity,
                    aa.municipality AS municipality_a, ba.municipality AS municipality_b,
                    aa.postal_code AS postal_a, ba.postal_code AS postal_b,
                    COALESCE(ai.afms, '{}') AS afms_a, COALESCE(bi.afms, '{}') AS afms_b,
                    COALESCE(ac.domains, '{}') AS domains_a, COALESCE(bc.domains, '{}') AS domains_b,
                    COALESCE(ac.phones, '{}') AS phones_a, COALESCE(bc.phones, '{}') AS phones_b,
                    COALESCE(ac.emails, '{}') AS emails_a, COALESCE(bc.emails, '{}') AS emails_b,
                    (
                        COALESCE(aa.valid_from, ac.valid_from, '-infinity'::timestamptz)
                            <= COALESCE(ba.valid_to, bc.valid_to, 'infinity'::timestamptz)
                        AND COALESCE(ba.valid_from, bc.valid_from, '-infinity'::timestamptz)
                            <= COALESCE(aa.valid_to, ac.valid_to, 'infinity'::timestamptz)
                    ) AS temporal_compatibility,
                    GREATEST(
                        COALESCE(aa.reliability, 0.70), COALESCE(ba.reliability, 0.70),
                        COALESCE(ac.reliability, 0.70), COALESCE(bc.reliability, 0.70),
                        COALESCE(anr.reliability, 0.70), COALESCE(bnr.reliability, 0.70)
                    ) AS source_reliability,
                    CASE
                        WHEN COALESCE(ac.domains, '{}') && COALESCE(bc.domains, '{}') THEN 'EXACT_DOMAIN'
                        WHEN COALESCE(ac.phones, '{}') && COALESCE(bc.phones, '{}') THEN 'EXACT_PHONE'
                        WHEN COALESCE(ac.emails, '{}') && COALESCE(bc.emails, '{}') THEN 'EXACT_EMAIL'
                        WHEN aa.postal_code IS NOT NULL AND aa.postal_code = ba.postal_code THEN 'POSTAL_AND_NAME'
                        ELSE 'SAME_TYPE_TRIGRAM_NAME'
                    END AS blocking_reason
                FROM candidate_pairs pair
                JOIN entities a ON a.id = pair.entity_a_id
                JOIN entities b ON b.id = pair.entity_b_id
                LEFT JOIN entity_afm ai ON ai.entity_id = a.id
                LEFT JOIN entity_afm bi ON bi.entity_id = b.id
                LEFT JOIN entity_address aa ON aa.entity_id = a.id
                LEFT JOIN entity_address ba ON ba.entity_id = b.id
                LEFT JOIN entity_contact ac ON ac.entity_id = a.id
                LEFT JOIN entity_contact bc ON bc.entity_id = b.id
                LEFT JOIN entity_name_reliability anr ON anr.entity_id = a.id
                LEFT JOIN entity_name_reliability bnr ON bnr.entity_id = b.id
                ORDER BY name_similarity DESC, a.id, b.id
                """
            ),
            {"minimum_similarity": minimum_name_similarity, "limit": limit},
        )
    ).all()

    written = 0
    conflicts = 0
    for row in rows:
        score, breakdown = score_candidate(row._mapping)
        if breakdown["identifier_conflict"]:
            conflicts += 1
        result = await conn.execute(
            pg_insert(entity_match_candidates)
            .values(
                id=uuid.uuid4(),
                entity_a_id=row.entity_a_id,
                entity_b_id=row.entity_b_id,
                score=Decimal(str(score)),
                score_breakdown=breakdown,
                blocking_reason=row.blocking_reason,
            )
            .on_conflict_do_update(
                index_elements=[
                    entity_match_candidates.c.entity_a_id,
                    entity_match_candidates.c.entity_b_id,
                ],
                set_={
                    "score": Decimal(str(score)),
                    "score_breakdown": breakdown,
                    "blocking_reason": row.blocking_reason,
                },
                where=entity_match_candidates.c.status == "PENDING_REVIEW",
            )
        )
        if result.rowcount:
            written += 1
    return CandidateGenerationResult(len(rows), written, conflicts)

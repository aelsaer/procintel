"""Derive evidence-backed early-demand signals from canonical procurement data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class SignalRefreshResult:
    early_requests: int
    expiring_contracts: int


async def refresh_derived_procurement_signals(
    conn: AsyncConnection,
    *,
    as_of: date | None = None,
) -> SignalRefreshResult:
    as_of = as_of or date.today()
    await conn.execute(
        sa.text(
            """
            UPDATE procurement_signals
            SET is_current = FALSE, updated_at = now()
            WHERE evidence ->> 'derived_by' = 'procintel.canonical'
            """
        )
    )
    early = await conn.execute(
        sa.text(
            """
            INSERT INTO procurement_signals (
                id, signal_type, title, description, buyer_entity_id,
                source_record_id, source_url, source_identifier,
                publication_date, expected_notice_date, estimated_value,
                currency, cpv_codes, nuts_codes, confidence, evidence,
                linked_process_id, is_current, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                CASE WHEN a.act_type = 'APPROVED_REQUEST' THEN 'BUDGET_APPROVAL' ELSE 'EARLY_REQUEST' END,
                COALESCE(a.title, pp.title, 'Πρώιμο αίτημα προμήθειας'),
                CASE
                    WHEN a.act_type = 'APPROVED_REQUEST' THEN 'Εγκεκριμένο αίτημα πριν από δημοσίευση προκήρυξης.'
                    ELSE 'Αίτημα προμήθειας πριν από δημοσίευση προκήρυξης.'
                END,
                pp.buyer_entity_id,
                a.source_record_id,
                sr.payload_uri,
                identifier.value_normalized,
                COALESCE(a.publication_date, a.decision_date, a.submission_date),
                COALESCE(a.submission_deadline, COALESCE(a.publication_date, a.decision_date, a.submission_date) + 90),
                COALESCE(a.amount_net, pp.estimated_value),
                COALESCE(a.currency, pp.currency, 'EUR'),
                COALESCE(cpv.codes, '{}'),
                COALESCE(loc.codes, '{}'),
                CASE WHEN a.act_type = 'APPROVED_REQUEST' THEN 0.90 ELSE 0.82 END,
                jsonb_build_object(
                    'derived_by', 'procintel.canonical',
                    'act_id', a.id,
                    'act_type', a.act_type,
                    'method', 'CANONICAL_ACT_TYPE'
                ),
                a.process_id,
                TRUE,
                now(),
                now()
            FROM procurement_acts a
            JOIN procurement_processes pp ON pp.id = a.process_id
            JOIN source_records sr ON sr.id = a.source_record_id
            LEFT JOIN LATERAL (
                SELECT ai.value_normalized
                FROM act_identifiers ai
                WHERE ai.act_id = a.id
                ORDER BY CASE ai.scheme WHEN 'ADAM' THEN 0 WHEN 'ADA' THEN 1 ELSE 2 END
                LIMIT 1
            ) identifier ON TRUE
            LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT value.cpv_code) AS codes
                FROM act_cpv_codes value WHERE value.act_id = a.id
            ) cpv ON TRUE
            LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT value.nuts_code) FILTER (WHERE value.nuts_code IS NOT NULL) AS codes
                FROM act_locations value WHERE value.act_id = a.id
            ) loc ON TRUE
            WHERE a.is_current
              AND a.act_type IN ('REQUEST', 'APPROVED_REQUEST')
              AND COALESCE(a.publication_date, a.decision_date, a.submission_date) >= CAST(:as_of AS date) - 730
              AND NOT EXISTS (
                  SELECT 1
                  FROM procurement_acts progressed
                  WHERE progressed.process_id = a.process_id
                    AND progressed.is_current
                    AND progressed.act_type IN ('NOTICE', 'AWARD', 'CONTRACT', 'CANCELLATION')
              )
            ON CONFLICT (source_record_id, signal_type) DO UPDATE SET
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                buyer_entity_id = EXCLUDED.buyer_entity_id,
                source_url = EXCLUDED.source_url,
                source_identifier = EXCLUDED.source_identifier,
                publication_date = EXCLUDED.publication_date,
                expected_notice_date = EXCLUDED.expected_notice_date,
                estimated_value = EXCLUDED.estimated_value,
                currency = EXCLUDED.currency,
                cpv_codes = EXCLUDED.cpv_codes,
                nuts_codes = EXCLUDED.nuts_codes,
                confidence = EXCLUDED.confidence,
                evidence = EXCLUDED.evidence,
                linked_process_id = EXCLUDED.linked_process_id,
                is_current = TRUE,
                updated_at = now()
            """
        ),
        {"as_of": as_of},
    )
    expiring = await conn.execute(
        sa.text(
            """
            INSERT INTO procurement_signals (
                id, signal_type, title, description, buyer_entity_id,
                source_record_id, source_url, source_identifier,
                publication_date, expected_notice_date, estimated_value,
                currency, cpv_codes, nuts_codes, confidence, evidence,
                linked_process_id, is_current, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                'EXPIRING_CONTRACT',
                COALESCE(a.title, pp.title, 'Σύμβαση που λήγει'),
                'Η τρέχουσα σύμβαση πλησιάζει στη λήξη της και μπορεί να δημιουργήσει ανάγκη ανανέωσης.',
                pp.buyer_entity_id,
                a.source_record_id,
                sr.payload_uri,
                identifier.value_normalized,
                a.publication_date,
                GREATEST(CAST(:as_of AS date), a.end_date - 120),
                COALESCE(a.amount_net, pp.current_contract_value),
                COALESCE(a.currency, pp.currency, 'EUR'),
                COALESCE(cpv.codes, '{}'),
                COALESCE(loc.codes, '{}'),
                0.76,
                jsonb_build_object(
                    'derived_by', 'procintel.canonical',
                    'act_id', a.id,
                    'act_type', a.act_type,
                    'contract_end_date', a.end_date,
                    'method', 'CONTRACT_END_DATE'
                ),
                a.process_id,
                TRUE,
                now(),
                now()
            FROM procurement_acts a
            JOIN procurement_processes pp ON pp.id = a.process_id
            JOIN source_records sr ON sr.id = a.source_record_id
            LEFT JOIN LATERAL (
                SELECT ai.value_normalized
                FROM act_identifiers ai
                WHERE ai.act_id = a.id
                ORDER BY CASE ai.scheme WHEN 'ADAM' THEN 0 WHEN 'ADA' THEN 1 ELSE 2 END
                LIMIT 1
            ) identifier ON TRUE
            LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT value.cpv_code) AS codes
                FROM act_cpv_codes value WHERE value.act_id = a.id
            ) cpv ON TRUE
            LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT value.nuts_code) FILTER (WHERE value.nuts_code IS NOT NULL) AS codes
                FROM act_locations value WHERE value.act_id = a.id
            ) loc ON TRUE
            WHERE a.is_current
              AND a.act_type = 'CONTRACT'
              AND a.end_date BETWEEN CAST(:as_of AS date) AND CAST(:as_of AS date) + 365
            ON CONFLICT (source_record_id, signal_type) DO UPDATE SET
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                buyer_entity_id = EXCLUDED.buyer_entity_id,
                source_url = EXCLUDED.source_url,
                source_identifier = EXCLUDED.source_identifier,
                publication_date = EXCLUDED.publication_date,
                expected_notice_date = EXCLUDED.expected_notice_date,
                estimated_value = EXCLUDED.estimated_value,
                currency = EXCLUDED.currency,
                cpv_codes = EXCLUDED.cpv_codes,
                nuts_codes = EXCLUDED.nuts_codes,
                confidence = EXCLUDED.confidence,
                evidence = EXCLUDED.evidence,
                linked_process_id = EXCLUDED.linked_process_id,
                is_current = TRUE,
                updated_at = now()
            """
        ),
        {"as_of": as_of},
    )
    await conn.commit()
    return SignalRefreshResult(
        early_requests=max(early.rowcount, 0),
        expiring_contracts=max(expiring.rowcount, 0),
    )

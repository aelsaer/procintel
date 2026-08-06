"""Idempotent data-quality checks for procurement analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class DataQualityResult:
    invalid_dates_repaired: int
    issues_opened: int
    issues_resolved: int
    by_code: dict[str, int]


def _scope_clause() -> str:
    return """
      AND (
        CAST(:date_from AS date) IS NULL
        OR COALESCE(
            a.decision_date,
            a.submission_date,
            a.publication_date,
            a.start_date,
            a.end_date,
            sr.fetched_at::date
        ) >= CAST(:date_from AS date)
      )
      AND (
        CAST(:date_to AS date) IS NULL
        OR COALESCE(
            a.decision_date,
            a.submission_date,
            a.publication_date,
            a.start_date,
            a.end_date,
            sr.fetched_at::date
        ) <= CAST(:date_to AS date)
      )
    """


async def _open_issue(
    conn: AsyncConnection,
    *,
    issue_code: str,
    severity: str,
    predicate: str,
    details_sql: str,
    params: dict,
) -> int:
    result = await conn.execute(
        sa.text(
            f"""
            INSERT INTO data_quality_issues (
                source_record_id, object_type, object_id,
                issue_code, severity, details
            )
            SELECT a.source_record_id, 'procurement_act', a.id,
                   :issue_code, :severity, {details_sql}
            FROM procurement_acts a
            JOIN source_records sr ON sr.id = a.source_record_id
            WHERE a.is_current = TRUE
              AND ({predicate})
              {_scope_clause()}
            ON CONFLICT DO NOTHING
            """
        ),
        {
            **params,
            "issue_code": issue_code,
            "severity": severity,
        },
    )
    return int(result.rowcount or 0)


async def _resolve_stale_issues(
    conn: AsyncConnection,
    *,
    issue_code: str,
    predicate: str,
    params: dict,
) -> int:
    result = await conn.execute(
        sa.text(
            f"""
            UPDATE data_quality_issues issue
            SET status = 'RESOLVED', resolved_at = now()
            FROM procurement_acts a
            JOIN source_records sr ON sr.id = a.source_record_id
            WHERE issue.object_type = 'procurement_act'
              AND issue.object_id = a.id
              AND issue.issue_code = :issue_code
              AND issue.status = 'OPEN'
              AND NOT (a.is_current = TRUE AND ({predicate}))
            """
        ),
        {**params, "issue_code": issue_code},
    )
    return int(result.rowcount or 0)


async def _sync_invalid_afm_issues(conn: AsyncConnection) -> tuple[int, int]:
    opened = await conn.execute(
        sa.text(
            """
            INSERT INTO data_quality_issues (
                id, source_record_id, object_type, object_id,
                issue_code, severity, details
            )
            SELECT gen_random_uuid(), identifier.source_record_id, 'ENTITY',
                   identifier.entity_id, 'INVALID_AFM_CHECKSUM', 'ERROR',
                   jsonb_build_object(
                       'value_raw', identifier.value_raw,
                       'value_normalized', identifier.value_normalized,
                       'match_eligibility', identifier.match_eligibility
                   )
            FROM entity_identifiers identifier
            WHERE identifier.scheme = 'AFM'
              AND identifier.is_current = TRUE
              AND identifier.identifier_valid = FALSE
            ON CONFLICT DO NOTHING
            """
        )
    )
    resolved = await conn.execute(
        sa.text(
            """
            UPDATE data_quality_issues issue
            SET status = 'RESOLVED', resolved_at = now()
            WHERE issue.object_type = 'ENTITY'
              AND issue.issue_code = 'INVALID_AFM_CHECKSUM'
              AND issue.status IN ('OPEN', 'ACKNOWLEDGED')
              AND NOT EXISTS (
                  SELECT 1
                  FROM entity_identifiers identifier
                  WHERE identifier.entity_id = issue.object_id
                    AND identifier.scheme = 'AFM'
                    AND identifier.is_current = TRUE
                    AND identifier.identifier_valid = FALSE
              )
            """
        )
    )
    return int(opened.rowcount or 0), int(resolved.rowcount or 0)


async def run_data_quality_checks(
    conn: AsyncConnection,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    repair_invalid_dates: bool = True,
) -> DataQualityResult:
    params = {"date_from": date_from, "date_to": date_to}
    current_year = date.today().year
    date_fields = (
        "decision_date",
        "submission_date",
        "publication_date",
        "start_date",
        "end_date",
    )
    invalid_predicates = [
        f"(a.{field} IS NOT NULL AND "
        f"(EXTRACT(YEAR FROM a.{field}) < 2000 "
        f"OR EXTRACT(YEAR FROM a.{field}) > CAST(:max_year AS INTEGER)))"
        for field in date_fields
    ]
    invalid_predicates.append(
        "(a.submission_deadline IS NOT NULL AND "
        "(EXTRACT(YEAR FROM a.submission_deadline) < 2000 "
        "OR EXTRACT(YEAR FROM a.submission_deadline) > "
        "CAST(:max_year AS INTEGER)))"
    )
    invalid_predicate = " OR ".join(invalid_predicates)
    invalid_details = """
        jsonb_strip_nulls(jsonb_build_object(
            'decision_date', a.decision_date,
            'submission_date', a.submission_date,
            'publication_date', a.publication_date,
            'start_date', a.start_date,
            'end_date', a.end_date,
            'submission_deadline', a.submission_deadline,
            'accepted_year_range',
            jsonb_build_array(2000, CAST(:max_year AS INTEGER))
        ))
    """
    by_code: dict[str, int] = {}
    invalid_afm_opened, invalid_afm_resolved = await _sync_invalid_afm_issues(conn)
    by_code["INVALID_AFM_CHECKSUM"] = invalid_afm_opened
    by_code["INVALID_DATE_RANGE"] = await _open_issue(
        conn,
        issue_code="INVALID_DATE_RANGE",
        severity="ERROR",
        predicate=invalid_predicate,
        details_sql=invalid_details,
        params={
            "date_from": None,
            "date_to": None,
            "max_year": current_year + 1,
        },
    )

    invalid_dates_repaired = 0
    if repair_invalid_dates:
        assignments = ", ".join(
            (
                f"{field} = CASE WHEN {field} IS NOT NULL AND "
                f"(EXTRACT(YEAR FROM {field}) < 2000 "
                f"OR EXTRACT(YEAR FROM {field}) > CAST(:max_year AS INTEGER)) "
                f"THEN NULL ELSE {field} END"
            )
            for field in date_fields
        )
        assignments += (
            ", submission_deadline = CASE WHEN submission_deadline IS NOT NULL "
            "AND (EXTRACT(YEAR FROM submission_deadline) < 2000 "
            "OR EXTRACT(YEAR FROM submission_deadline) > "
            "CAST(:max_year AS INTEGER)) "
            "THEN NULL ELSE submission_deadline END"
        )
        repair = await conn.execute(
            sa.text(
                f"""
                UPDATE procurement_acts
                SET {assignments}, updated_at = now()
                WHERE {invalid_predicate.replace("a.", "")}
                """
            ),
            {"max_year": current_year + 1},
        )
        invalid_dates_repaired = int(repair.rowcount or 0)
    issues_resolved = invalid_afm_resolved + await _resolve_stale_issues(
        conn,
        issue_code="INVALID_DATE_RANGE",
        predicate=invalid_predicate,
        params={"max_year": current_year + 1},
    )

    rules = (
        (
            "END_BEFORE_START",
            "ERROR",
            "a.end_date IS NOT NULL AND a.start_date IS NOT NULL "
            "AND a.end_date < a.start_date",
            "jsonb_build_object('start', a.start_date, 'end', a.end_date)",
        ),
        (
            "GROSS_BELOW_NET",
            "ERROR",
            "a.amount_gross IS NOT NULL AND a.amount_net IS NOT NULL "
            "AND ABS(a.amount_gross) < ABS(a.amount_net)",
            "jsonb_build_object('amount_net', a.amount_net, 'amount_gross', a.amount_gross)",
        ),
        (
            "NEGATIVE_PROCUREMENT_VALUE",
            "ERROR",
            "a.act_type IN ('REQUEST', 'NOTICE', 'AWARD', 'CONTRACT', 'TED_NOTICE') "
            "AND (a.amount_net < 0 OR a.amount_gross < 0)",
            "jsonb_build_object('act_type', a.act_type, "
            "'amount_net', a.amount_net, 'amount_gross', a.amount_gross)",
        ),
        (
            "IMPLAUSIBLE_PROCUREMENT_VALUE",
            "ERROR",
            "a.act_type IN ('REQUEST', 'NOTICE', 'AWARD', 'CONTRACT', 'TED_NOTICE') "
            "AND (ABS(a.amount_net) > 1000000000000 "
            "OR ABS(a.amount_gross) > 1000000000000)",
            "jsonb_build_object('act_type', a.act_type, "
            "'amount_net', a.amount_net, 'amount_gross', a.amount_gross, "
            "'maximum_accepted_value', 1000000000000)",
        ),
        (
            "LIFECYCLE_DATE_ORDER",
            "ERROR",
            "EXISTS ("
            "SELECT 1 FROM act_links link "
            "JOIN procurement_acts predecessor ON predecessor.id = CASE "
            "WHEN link.link_type = 'ANNOUNCES' AND link.to_act_id = a.id "
            "THEN link.from_act_id "
            "WHEN link.link_type = 'AWARDS' AND link.from_act_id = a.id "
            "THEN link.to_act_id "
            "WHEN link.link_type = 'EXECUTES' AND link.to_act_id = a.id "
            "THEN link.from_act_id "
            "WHEN link.link_type IN ('AMENDS', 'EXTENDS', 'PAYS') "
            "AND link.from_act_id = a.id THEN link.to_act_id END "
            "WHERE predecessor.is_current = TRUE "
            "AND ((link.link_type = 'ANNOUNCES' AND link.to_act_id = a.id) "
            "OR (link.link_type = 'AWARDS' AND link.from_act_id = a.id) "
            "OR (link.link_type = 'EXECUTES' AND link.to_act_id = a.id) "
            "OR (link.link_type IN ('AMENDS', 'EXTENDS', 'PAYS') "
            "AND link.from_act_id = a.id)) "
            "AND COALESCE(a.decision_date, a.publication_date, "
            "a.submission_date, a.start_date) < "
            "COALESCE(predecessor.decision_date, predecessor.publication_date, "
            "predecessor.submission_date, predecessor.start_date))",
            "jsonb_build_object('act_type', a.act_type, "
            "'event_date', COALESCE(a.decision_date, a.publication_date, "
            "a.submission_date, a.start_date), "
            "'reason', 'linked predecessor has a later event date')",
        ),
        (
            "MISSING_EVENT_DATE",
            "WARNING",
            "sr.source_system IN ('KHMDHS', 'DIAVGEIA', 'TED') "
            "AND sr.resource_type <> 'adamChain' "
            "AND a.decision_date IS NULL AND a.submission_date IS NULL "
            "AND a.publication_date IS NULL AND a.start_date IS NULL",
            "jsonb_build_object('source_system', sr.source_system, 'resource_type', sr.resource_type)",
        ),
        (
            "MISSING_CPV",
            "WARNING",
            "a.act_type IN ('REQUEST', 'NOTICE', 'AWARD', 'CONTRACT', 'TED_NOTICE') "
            "AND NOT EXISTS (SELECT 1 FROM act_cpv_codes cpv WHERE cpv.act_id = a.id)",
            "jsonb_build_object('act_type', a.act_type)",
        ),
        (
            "MISSING_SUPPLIER",
            "INFO",
            "a.act_type IN ('AWARD', 'CONTRACT') "
            "AND NOT EXISTS (SELECT 1 FROM act_parties party "
            "WHERE party.act_id = a.id AND party.party_role IN ('SUPPLIER', 'CONTRACTOR'))",
            "jsonb_build_object('act_type', a.act_type)",
        ),
        (
            "MISSING_OFFICIAL_DOCUMENT",
            "INFO",
            "a.act_type IN ('NOTICE', 'AWARD', 'CONTRACT') "
            "AND NOT EXISTS (SELECT 1 FROM documents document WHERE document.act_id = a.id)",
            "jsonb_build_object('act_type', a.act_type)",
        ),
    )
    for code, severity, predicate, details_sql in rules:
        by_code[code] = await _open_issue(
            conn,
            issue_code=code,
            severity=severity,
            predicate=predicate,
            details_sql=details_sql,
            params=params,
        )
        issues_resolved += await _resolve_stale_issues(
            conn,
            issue_code=code,
            predicate=predicate,
            params=params,
        )

    await conn.commit()
    return DataQualityResult(
        invalid_dates_repaired=invalid_dates_repaired,
        issues_opened=sum(by_code.values()),
        issues_resolved=issues_resolved,
        by_code=by_code,
    )

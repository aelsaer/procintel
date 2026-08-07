"""Tenant-relative opportunity scoring.

The product spec frames opportunities as "fit for this business", not a
global ranking. This module reads the tenant's active opportunity alert rules
as the first business-profile representation and writes `opportunity_scores`.
It is intentionally explainable: every score stores the matched rule and the
signals used, and it is not exposed as win probability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    alert_rules,
    business_profiles,
    opportunity_relevance_feedback,
    opportunity_scores,
)
from services.alerts.evaluate import rule_matches
from services.search_index.lexical import lexical_query_matches, query_concept_pattern


@dataclass(frozen=True)
class OpportunityScoreRun:
    tenant_id: uuid.UUID
    rules_considered: int
    candidates_seen: int
    scores_written: int


async def tenant_ids_with_business_profiles(
    conn: AsyncConnection,
) -> list[uuid.UUID]:
    """Return only tenants that have completed the persisted profile step."""
    return list(
        (
            await conn.execute(
                sa.select(business_profiles.c.tenant_id).order_by(
                    business_profiles.c.tenant_id
                )
            )
        )
        .scalars()
        .all()
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def candidate_taxonomy_scope(rules: list[Any]) -> tuple[list[str], list[str]]:
    """Build a safe taxonomy superset for SQL candidate preselection."""
    cpv_likes: list[str] = []
    keyword_patterns: list[str] = []
    for rule in rules:
        filters = rule.filters or {}
        cpv_prefixes = _as_list(filters.get("cpv_prefix")) + _as_list(filters.get("cpv_prefixes"))
        keywords = _as_list(filters.get("keyword")) + _as_list(filters.get("keywords"))
        patterns = [
            pattern
            for keyword in keywords
            if (pattern := query_concept_pattern(str(keyword))) is not None
        ]
        mode = str(filters.get("taxonomy_match_mode") or "ANY").upper()
        if mode == "KEYWORD_REQUIRED" and patterns:
            rule_cpv_likes: list[str] = []
        else:
            rule_cpv_likes = [f"{str(prefix).split('-', 1)[0]}%" for prefix in cpv_prefixes]
        if not rule_cpv_likes and not patterns:
            return [], []
        cpv_likes.extend(rule_cpv_likes)
        keyword_patterns.extend(patterns)
    return list(dict.fromkeys(cpv_likes)), list(dict.fromkeys(keyword_patterns))


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _score_timing(latest_act_date: date | None, as_of: date) -> Decimal:
    if latest_act_date is None:
        return Decimal("30.00")
    age_days = max((as_of - latest_act_date).days, 0)
    if age_days <= 7:
        return Decimal("100.00")
    if age_days <= 30:
        return Decimal("85.00")
    if age_days <= 90:
        return Decimal("65.00")
    return Decimal("40.00")


def _score_value(amount: Decimal | None, filters: dict[str, Any]) -> Decimal:
    amount_min = _money(filters.get("amount_min"))
    amount_max = _money(filters.get("amount_max"))
    if amount is None:
        return Decimal("35.00")
    if amount_min is None and amount_max is None:
        return Decimal("60.00")
    if amount_min is not None and amount < amount_min:
        return Decimal("20.00")
    if amount_max is not None and amount > amount_max:
        return Decimal("20.00")
    return Decimal("100.00")


def _score_data_confidence(context: dict[str, Any]) -> Decimal:
    points = Decimal("20.00")
    if context.get("buyer_id"):
        points += Decimal("20.00")
    if context.get("cpv_codes"):
        points += Decimal("20.00")
    if context.get("amount_gross") is not None:
        points += Decimal("20.00")
    if context.get("nuts_codes"):
        points += Decimal("10.00")
    if context.get("act_count", 0) > 1:
        points += Decimal("10.00")
    return min(points, Decimal("100.00"))


def _score_candidate(
    *,
    rule_name: str,
    filters: dict[str, Any],
    context: dict[str, Any],
    latest_act_date: date | None,
    supplier_count: int,
    as_of: date,
) -> dict[str, Any]:
    cpv_prefixes = _as_list(filters.get("cpv_prefix")) + _as_list(filters.get("cpv_prefixes"))
    cpv_matched = any(
        str(code).startswith(str(prefix))
        for code in context.get("cpv_codes", [])
        for prefix in cpv_prefixes
    )
    keywords = _as_list(filters.get("keyword")) + _as_list(filters.get("keywords"))
    keyword_matched = any(
        lexical_query_matches(str(keyword), context.get("title"))
        for keyword in keywords
    )
    buyer_affinity_score = Decimal("90.00") if filters.get("buyer_id") else Decimal("55.00")
    if _as_list(filters.get("nuts_code")) or _as_list(filters.get("nuts_codes")):
        buyer_affinity_score = max(buyer_affinity_score, Decimal("75.00"))

    competitive_score = Decimal("75.00") if supplier_count == 0 else Decimal("45.00")
    if cpv_matched and keyword_matched:
        cpv_score = Decimal("100.00")
        taxonomy_match_method = "CPV_AND_TITLE_KEYWORD"
    elif keyword_matched:
        cpv_score = Decimal("75.00")
        taxonomy_match_method = "TITLE_KEYWORD_FALLBACK"
    elif cpv_matched:
        cpv_score = Decimal("100.00")
        taxonomy_match_method = "CPV"
    else:
        cpv_score = Decimal("50.00")
        taxonomy_match_method = "UNFILTERED"
    timing_score = _score_timing(latest_act_date, as_of)
    value_score = _score_value(context.get("amount_gross"), filters)
    confidence_score = _score_data_confidence(context)

    total = (
        cpv_score * Decimal("0.25")
        + buyer_affinity_score * Decimal("0.20")
        + timing_score * Decimal("0.15")
        + competitive_score * Decimal("0.15")
        + value_score * Decimal("0.15")
        + confidence_score * Decimal("0.10")
    ).quantize(Decimal("0.01"))

    evidence = [
        {"signal": "matched_rule", "value": rule_name},
        {"signal": "cpv_codes", "value": context.get("cpv_codes", [])},
        {"signal": "taxonomy_match_method", "value": taxonomy_match_method},
        {"signal": "nuts_codes", "value": context.get("nuts_codes", [])},
        {
            "signal": "amount_gross",
            "value": str(context.get("amount_gross")) if context.get("amount_gross") is not None else None,
        },
        {"signal": "latest_act_date", "value": latest_act_date.isoformat() if latest_act_date else None},
        {"signal": "supplier_count_so_far", "value": supplier_count},
    ]

    return {
        "total_score": total,
        "cpv_company_fit_score": cpv_score,
        "buyer_affinity_score": buyer_affinity_score,
        "timing_score": timing_score,
        "competitive_attractiveness_score": competitive_score,
        "contract_value_fit_score": value_score,
        "data_confidence_score": confidence_score,
        "evidence": evidence,
    }


async def _load_opportunity_rules(
    conn: AsyncConnection,
    tenant_id: uuid.UUID,
) -> tuple[list[Any], int]:
    profile = (
        await conn.execute(
            sa.select(business_profiles).where(business_profiles.c.tenant_id == tenant_id)
        )
    ).first()
    if profile is not None and (profile.cpv_prefixes or profile.keywords):
        filters: dict[str, Any] = {
            "cpv_prefixes": profile.cpv_prefixes or [],
            "keywords": profile.keywords or [],
            "excluded_cpv_prefixes": profile.excluded_cpv_prefixes or [],
            "excluded_keywords": profile.excluded_keywords or [],
            "taxonomy_match_mode": "KEYWORD_REQUIRED",
            "nuts_codes": profile.nuts_codes or [],
            "municipality": profile.municipality,
            "amount_min": profile.amount_min,
            "amount_max": profile.amount_max,
        }
        return (
            [SimpleNamespace(name=profile.company_name or "Business profile", filters=filters)],
            int(profile.classification_version),
        )

    rows = (
        await conn.execute(
            sa.select(alert_rules.c.id, alert_rules.c.name, alert_rules.c.filters).where(
                alert_rules.c.tenant_id == tenant_id,
                alert_rules.c.is_active.is_(True),
                sa.or_(
                    alert_rules.c.event_types.any("opportunity.created"),
                    alert_rules.c.event_types.any("opportunity.updated"),
                ),
            )
        )
    ).all()
    return rows, int(profile.classification_version) if profile is not None else 1


async def _load_candidates(
    conn: AsyncConnection,
    *,
    as_of: date,
    lookback_days: int,
    include_contracted: bool,
    limit: int | None,
    candidate_cpv_likes: list[str] | None = None,
    candidate_keyword_patterns: list[str] | None = None,
) -> list[Any]:
    since_date = as_of - timedelta(days=lookback_days) if lookback_days > 0 else None
    limit_clause = "LIMIT :limit" if limit is not None else ""
    sql = sa.text(
        f"""
        WITH candidate_processes AS MATERIALIZED (
            SELECT
                opportunity.process_id,
                MAX(COALESCE(
                    opportunity.publication_date,
                    opportunity.submission_date,
                    opportunity.decision_date
                )) AS latest_act_date
            FROM procurement_acts opportunity
            JOIN procurement_processes process ON process.id = opportunity.process_id
            WHERE process.record_status = 'ACTIVE'
              AND opportunity.is_current = TRUE
              AND opportunity.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')
              AND procintel_act_is_analytics_eligible(opportunity.id)
              AND (
                  NOT CAST(:has_candidate_taxonomy AS BOOLEAN)
                  OR procintel_taxonomy_match(
                      opportunity.id,
                      opportunity.title,
                      CAST(:candidate_cpv_likes AS TEXT[]),
                      CAST(:candidate_keyword_patterns AS TEXT[]),
                      FALSE
                  )
              )
              AND (
                  CAST(:since_date AS DATE) IS NULL
                  OR COALESCE(
                      opportunity.publication_date,
                      opportunity.submission_date,
                      opportunity.decision_date
                  ) >= CAST(:since_date AS DATE)
              )
              AND (
                  CAST(:include_contracted AS BOOLEAN)
                  OR NOT EXISTS (
                      SELECT 1
                      FROM procurement_acts contract
                      WHERE contract.process_id = opportunity.process_id
                        AND contract.is_current = TRUE
                        AND contract.act_type = 'CONTRACT'
                        AND procintel_act_is_analytics_eligible(contract.id)
                  )
              )
            GROUP BY opportunity.process_id
            ORDER BY latest_act_date DESC NULLS LAST, opportunity.process_id
            {limit_clause}
        )
        SELECT
            process.id AS process_id,
            process.title,
            process.buyer_entity_id,
            COALESCE(
                process.estimated_value,
                process.awarded_value,
                process.current_contract_value,
                act_stats.amount_gross
            ) AS amount_gross,
            candidate.latest_act_date,
            COALESCE(act_stats.act_count, 0) AS act_count,
            COALESCE(cpv.cpv_codes, ARRAY[]::TEXT[]) AS cpv_codes,
            COALESCE(location.nuts_codes, ARRAY[]::TEXT[]) AS nuts_codes,
            COALESCE(location.location_names, ARRAY[]::TEXT[]) AS location_names,
            COALESCE(supplier.supplier_count, 0) AS supplier_count
        FROM candidate_processes candidate
        JOIN procurement_processes process ON process.id = candidate.process_id
        LEFT JOIN LATERAL (
            SELECT
                MAX(COALESCE(act.amount_gross, act.amount_net)) AS amount_gross,
                COUNT(*)::INT AS act_count
            FROM procurement_acts act
            WHERE act.process_id = candidate.process_id
              AND act.is_current = TRUE
              AND procintel_act_is_analytics_eligible(act.id)
        ) act_stats ON TRUE
        LEFT JOIN LATERAL (
            SELECT ARRAY_AGG(DISTINCT code.cpv_code ORDER BY code.cpv_code) AS cpv_codes
            FROM procurement_acts act
            JOIN act_cpv_codes code ON code.act_id = act.id
            WHERE act.process_id = candidate.process_id
              AND act.is_current = TRUE
              AND procintel_act_is_analytics_eligible(act.id)
        ) cpv ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT place.nuts_code), NULL) AS nuts_codes,
                ARRAY_REMOVE(
                    ARRAY_AGG(DISTINCT COALESCE(
                        place.municipality_name,
                        place.place_text,
                        place.regional_unit_name,
                        place.region_name
                    )),
                    NULL
                ) AS location_names
            FROM procurement_acts act
            JOIN act_locations place ON place.act_id = act.id
            WHERE act.process_id = candidate.process_id
              AND act.is_current = TRUE
              AND procintel_act_is_analytics_eligible(act.id)
        ) location ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT party.entity_id)::INT AS supplier_count
            FROM procurement_acts act
            JOIN act_parties party
              ON party.act_id = act.id
             AND party.party_role IN ('SUPPLIER', 'CONTRACTOR')
            WHERE act.process_id = candidate.process_id
              AND act.is_current = TRUE
              AND procintel_act_is_analytics_eligible(act.id)
        ) supplier ON TRUE
        ORDER BY candidate.latest_act_date DESC NULLS LAST, process.id
        """
    )
    params = {
        "include_contracted": include_contracted,
        "since_date": since_date,
        "limit": limit,
        "has_candidate_taxonomy": bool(candidate_cpv_likes or candidate_keyword_patterns),
        "candidate_cpv_likes": candidate_cpv_likes or [],
        "candidate_keyword_patterns": candidate_keyword_patterns or [],
    }
    return (await conn.execute(sql, params)).all()


async def score_opportunities_for_tenant(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    as_of: date | None = None,
    lookback_days: int = 120,
    include_contracted: bool = False,
    limit: int | None = None,
    clear_existing: bool | None = None,
) -> OpportunityScoreRun:
    """Recomputes opportunity scores for one tenant.

    By default a full recompute clears old scores first. Limited runs keep
    existing rows to avoid erasing scores outside the budgeted sample.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be non-negative")
    as_of = as_of or datetime.now(timezone.utc).date()
    clear_existing = limit is None if clear_existing is None else clear_existing

    rules, profile_version = await _load_opportunity_rules(conn, tenant_id)
    if not rules:
        if clear_existing:
            await conn.execute(
                opportunity_scores.delete().where(
                    opportunity_scores.c.tenant_id == tenant_id
                )
            )
            await conn.commit()
        return OpportunityScoreRun(
            tenant_id=tenant_id,
            rules_considered=0,
            candidates_seen=0,
            scores_written=0,
        )

    candidate_cpv_likes, candidate_keyword_patterns = candidate_taxonomy_scope(rules)
    candidates = await _load_candidates(
        conn,
        as_of=as_of,
        lookback_days=lookback_days,
        include_contracted=include_contracted,
        limit=limit,
        candidate_cpv_likes=candidate_cpv_likes,
        candidate_keyword_patterns=candidate_keyword_patterns,
    )
    feedback_rows = (
        await conn.execute(
            sa.select(
                opportunity_relevance_feedback.c.process_id,
                opportunity_relevance_feedback.c.label,
            ).where(opportunity_relevance_feedback.c.tenant_id == tenant_id)
            .where(opportunity_relevance_feedback.c.profile_version == profile_version)
        )
    ).all()
    feedback_by_process = {row.process_id: row.label for row in feedback_rows}

    written = 0
    written_process_ids: list[uuid.UUID] = []
    now = datetime.now(timezone.utc)
    for candidate in candidates:
        feedback_label = feedback_by_process.get(candidate.process_id)
        if feedback_label == "IRRELEVANT":
            continue
        context = {
            "title": candidate.title,
            "buyer_id": str(candidate.buyer_entity_id) if candidate.buyer_entity_id else None,
            "supplier_id": None,
            "amount_gross": candidate.amount_gross,
            "cpv_codes": candidate.cpv_codes or [],
            "nuts_codes": candidate.nuts_codes or [],
            "location_names": candidate.location_names or [],
            "act_count": candidate.act_count,
        }
        best_score: dict[str, Any] | None = None
        for rule in rules:
            filters = rule.filters or {}
            if not rule_matches(filters, context):
                continue
            score = _score_candidate(
                rule_name=rule.name,
                filters=filters,
                context=context,
                latest_act_date=candidate.latest_act_date,
                supplier_count=candidate.supplier_count or 0,
                as_of=as_of,
            )
            if best_score is None or score["total_score"] > best_score["total_score"]:
                best_score = score

        if best_score is None and feedback_label == "RELEVANT":
            first_rule = rules[0]
            best_score = _score_candidate(
                rule_name=first_rule.name,
                filters=first_rule.filters or {},
                context=context,
                latest_act_date=candidate.latest_act_date,
                supplier_count=candidate.supplier_count or 0,
                as_of=as_of,
            )
        if best_score is None:
            continue
        if feedback_label == "RELEVANT":
            best_score["total_score"] = min(
                best_score["total_score"] + Decimal("5.00"),
                Decimal("100.00"),
            )
            best_score["evidence"] = [
                *best_score["evidence"],
                {
                    "signal": "tenant_relevance_feedback",
                    "value": "RELEVANT",
                    "effect": "+5 score and retained in radar",
                },
            ]

        score_id = uuid.uuid4()
        insert_stmt = (
            pg_insert(opportunity_scores)
            .values(
                id=score_id,
                process_id=candidate.process_id,
                tenant_id=tenant_id,
                profile_version=profile_version,
                computed_at=now,
                **best_score,
            )
            .on_conflict_do_update(
                index_elements=["process_id", "tenant_id"],
                set_={
                    **best_score,
                    "profile_version": profile_version,
                    "computed_at": now,
                },
            )
        )
        await conn.execute(insert_stmt)
        written += 1
        written_process_ids.append(candidate.process_id)

    if clear_existing:
        delete_stale = opportunity_scores.delete().where(
            opportunity_scores.c.tenant_id == tenant_id,
        )
        if written_process_ids:
            delete_stale = delete_stale.where(
                opportunity_scores.c.process_id.not_in(written_process_ids)
            )
        await conn.execute(delete_stale)

    await conn.commit()
    return OpportunityScoreRun(
        tenant_id=tenant_id,
        rules_considered=len(rules),
        candidates_seen=len(candidates),
        scores_written=written,
    )

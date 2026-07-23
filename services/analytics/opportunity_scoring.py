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

from packages.domain.tables import alert_rules, business_profiles, opportunity_scores
from services.alerts.evaluate import rule_matches
from services.search_index.lexical import lexical_query_matches


@dataclass(frozen=True)
class OpportunityScoreRun:
    tenant_id: uuid.UUID
    rules_considered: int
    candidates_seen: int
    scores_written: int


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


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


async def _load_opportunity_rules(conn: AsyncConnection, tenant_id: uuid.UUID) -> list[Any]:
    profile = (
        await conn.execute(
            sa.select(business_profiles).where(business_profiles.c.tenant_id == tenant_id)
        )
    ).first()
    if profile is not None and (profile.cpv_prefixes or profile.keywords):
        filters: dict[str, Any] = {
            "cpv_prefixes": profile.cpv_prefixes or [],
            "keywords": profile.keywords or [],
            "taxonomy_match_mode": "KEYWORD_REQUIRED",
            "nuts_codes": profile.nuts_codes or [],
            "municipality": profile.municipality,
            "amount_min": profile.amount_min,
            "amount_max": profile.amount_max,
        }
        return [SimpleNamespace(name=profile.company_name or "Business profile", filters=filters)]

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
    return rows


async def _load_candidates(
    conn: AsyncConnection,
    *,
    as_of: date,
    lookback_days: int,
    include_contracted: bool,
    limit: int | None,
) -> list[Any]:
    since_date = as_of - timedelta(days=lookback_days) if lookback_days > 0 else None
    limit_clause = "LIMIT :limit" if limit is not None else ""
    sql = sa.text(
        f"""
        WITH process_rollup AS (
            SELECT
                pp.id AS process_id,
                pp.title,
                pp.buyer_entity_id,
                COALESCE(pp.estimated_value, pp.awarded_value, pp.current_contract_value, MAX(a.amount_gross)) AS amount_gross,
                BOOL_OR(a.act_type = 'CONTRACT') AS has_contract,
                BOOL_OR(a.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')) AS has_opportunity,
                MAX(COALESCE(a.publication_date, a.submission_date, a.decision_date)) AS latest_act_date,
                COUNT(DISTINCT a.id) AS act_count,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT acpv.cpv_code), NULL) AS cpv_codes,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT aloc.nuts_code), NULL) AS nuts_codes,
                ARRAY_REMOVE(
                    ARRAY_AGG(DISTINCT COALESCE(
                        aloc.municipality_name,
                        aloc.place_text,
                        aloc.regional_unit_name,
                        aloc.region_name
                    )),
                    NULL
                ) AS location_names,
                COUNT(DISTINCT supplier_ap.entity_id) AS supplier_count
            FROM procurement_processes pp
            JOIN procurement_acts a ON a.process_id = pp.id AND a.is_current = TRUE
            LEFT JOIN act_cpv_codes acpv ON acpv.act_id = a.id
            LEFT JOIN act_locations aloc ON aloc.act_id = a.id
            LEFT JOIN act_parties supplier_ap
                ON supplier_ap.act_id = a.id
               AND supplier_ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
            WHERE pp.record_status = 'ACTIVE'
            GROUP BY pp.id, pp.title, pp.buyer_entity_id, pp.estimated_value, pp.awarded_value, pp.current_contract_value
        )
        SELECT *
        FROM process_rollup
        WHERE has_opportunity = TRUE
          AND (CAST(:include_contracted AS BOOLEAN) OR has_contract = FALSE)
          AND (CAST(:since_date AS DATE) IS NULL OR latest_act_date >= CAST(:since_date AS DATE))
        ORDER BY latest_act_date DESC NULLS LAST, process_id
        {limit_clause}
        """
    )
    params = {"include_contracted": include_contracted, "since_date": since_date, "limit": limit}
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

    rules = await _load_opportunity_rules(conn, tenant_id)
    if not rules:
        return OpportunityScoreRun(tenant_id=tenant_id, rules_considered=0, candidates_seen=0, scores_written=0)

    if clear_existing:
        await conn.execute(opportunity_scores.delete().where(opportunity_scores.c.tenant_id == tenant_id))
        await conn.commit()

    candidates = await _load_candidates(
        conn,
        as_of=as_of,
        lookback_days=lookback_days,
        include_contracted=include_contracted,
        limit=limit,
    )

    written = 0
    now = datetime.now(timezone.utc)
    for candidate in candidates:
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

        if best_score is None:
            continue

        score_id = uuid.uuid4()
        insert_stmt = (
            pg_insert(opportunity_scores)
            .values(
                id=score_id,
                process_id=candidate.process_id,
                tenant_id=tenant_id,
                computed_at=now,
                **best_score,
            )
            .on_conflict_do_update(
                index_elements=["process_id", "tenant_id"],
                set_={**best_score, "computed_at": now},
            )
        )
        await conn.execute(insert_stmt)
        written += 1

    await conn.commit()
    return OpportunityScoreRun(
        tenant_id=tenant_id,
        rules_considered=len(rules),
        candidates_seen=len(candidates),
        scores_written=written,
    )

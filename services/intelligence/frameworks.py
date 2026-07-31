"""Framework-agreement materialization and route-to-market scoring."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection


def framework_window_status(
    valid_until: date | None,
    *,
    today: date,
    reopening_days: int = 180,
) -> str:
    if valid_until is None:
        return "UNKNOWN"
    days = (valid_until - today).days
    if days < 0:
        return "EXPIRED"
    if days <= reopening_days:
        return "REOPENING"
    return "ACTIVE"


def framework_relevance_score(
    *,
    cpv_codes: list[str],
    profile_cpv_prefixes: list[str],
    realized_spend: Decimal | float | int,
    ceiling_amount: Decimal | float | int | None,
    status: str,
) -> float:
    normalized = [code.split("-", 1)[0].strip() for code in cpv_codes if code.strip()]
    prefixes = [prefix.split("-", 1)[0].strip() for prefix in profile_cpv_prefixes if prefix.strip()]
    cpv_fit = 1.0 if not prefixes else max(
        (len(prefix) / 8 for prefix in prefixes if any(code.startswith(prefix) for code in normalized)),
        default=0.0,
    )
    ceiling = float(ceiling_amount or 0)
    utilization = min(1.0, float(realized_spend or 0) / ceiling) if ceiling > 0 else 0.0
    lifecycle = {"REOPENING": 1.0, "ACTIVE": 0.75, "UNKNOWN": 0.4, "EXPIRED": 0.05}.get(status, 0.2)
    return round(100 * (0.65 * cpv_fit + 0.2 * lifecycle + 0.15 * utilization), 2)


async def refresh_framework_memberships(conn: AsyncConnection) -> int:
    """Materialize suppliers explicitly published on framework agreement acts."""
    from packages.domain.tables import (
        act_parties,
        framework_supplier_memberships,
        procurement_acts,
    )

    rows = (
        await conn.execute(
            sa.select(
                procurement_acts.c.id.label("framework_act_id"),
                procurement_acts.c.start_date,
                procurement_acts.c.end_date,
                procurement_acts.c.source_record_id,
                procurement_acts.c.observed_at
                if "observed_at" in procurement_acts.c
                else procurement_acts.c.updated_at,
                act_parties.c.entity_id,
                act_parties.c.lot_id,
                act_parties.c.amount,
            )
            .select_from(
                procurement_acts.join(
                    act_parties,
                    act_parties.c.act_id == procurement_acts.c.id,
                )
            )
            .where(
                procurement_acts.c.agreement_type == "FRAMEWORK_AGREEMENT",
                act_parties.c.party_role.in_(("SUPPLIER", "CONTRACTOR", "CONSORTIUM_MEMBER")),
            )
        )
    ).all()
    for row in rows:
        lot_identifier = str(row.lot_id) if row.lot_id else None
        statement = pg_insert(framework_supplier_memberships).values(
            id=uuid.uuid4(),
            framework_act_id=row.framework_act_id,
            supplier_entity_id=row.entity_id,
            lot_identifier=lot_identifier,
            membership_status=(
                "EXPIRED" if row.end_date and row.end_date < date.today() else "ACTIVE"
            ),
            awarded_value=row.amount,
            valid_from=row.start_date,
            valid_until=row.end_date,
            source_record_id=row.source_record_id,
            evidence={"party_role": "published_on_framework_act"},
            observed_at=row.updated_at,
        )
        await conn.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    framework_supplier_memberships.c.framework_act_id,
                    framework_supplier_memberships.c.supplier_entity_id,
                    sa.func.coalesce(framework_supplier_memberships.c.lot_identifier, ""),
                ],
                set_={
                    "membership_status": statement.excluded.membership_status,
                    "awarded_value": statement.excluded.awarded_value,
                    "valid_from": statement.excluded.valid_from,
                    "valid_until": statement.excluded.valid_until,
                    "source_record_id": statement.excluded.source_record_id,
                    "evidence": statement.excluded.evidence,
                    "observed_at": statement.excluded.observed_at,
                },
            )
        )
    return len(rows)

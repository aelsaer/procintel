"""Query helpers shared by more than one router — kept out of routers/ so
they're reusable without importing FastAPI machinery."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import act_identifiers, act_parties, entities, entity_identifiers


async def load_identifiers(conn: AsyncConnection, act_id: uuid.UUID) -> dict[str, list[str]]:
    rows = (
        await conn.execute(
            select(act_identifiers.c.scheme, act_identifiers.c.value_normalized).where(
                act_identifiers.c.act_id == act_id
            )
        )
    ).all()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row.scheme, []).append(row.value_normalized)
    return result


async def load_entity_vat(conn: AsyncConnection, entity_id: uuid.UUID) -> str | None:
    row = (
        await conn.execute(
            select(entity_identifiers.c.value_normalized).where(
                entity_identifiers.c.entity_id == entity_id,
                entity_identifiers.c.scheme == "AFM",
                entity_identifiers.c.is_current.is_(True),
            )
        )
    ).first()
    return row.value_normalized if row is not None else None


async def load_parties(conn: AsyncConnection, act_id: uuid.UUID) -> tuple[dict | None, list[dict]]:
    """Returns (buyer, suppliers) as plain dicts ready for PartyResponse(**d)."""
    rows = (
        await conn.execute(
            select(
                act_parties.c.party_role,
                act_parties.c.amount,
                entities.c.id,
                entities.c.canonical_name,
            )
            .select_from(act_parties.join(entities, entities.c.id == act_parties.c.entity_id))
            .where(act_parties.c.act_id == act_id)
        )
    ).all()

    buyer: dict | None = None
    suppliers: list[dict] = []
    for row in rows:
        vat = await load_entity_vat(conn, row.id)
        party = {"id": str(row.id), "name": row.canonical_name, "vat": vat, "amount": row.amount}
        if row.party_role in ("BUYER", "CONTRACTING_AUTHORITY"):
            buyer = party
        elif row.party_role in ("SUPPLIER", "CONTRACTOR"):
            suppliers.append(party)
    return buyer, suppliers


def parse_uuid_or_422(value: str, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} must be a UUID") from exc

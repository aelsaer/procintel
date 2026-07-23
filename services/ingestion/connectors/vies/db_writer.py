"""Canonical writes for VIES checks — description.txt §3.9, §7.2.

Append-only: every check is its own `entity_vies_checks` row (a validation
*history*, not a snapshot to overwrite — consistent with never treating
VIES as a company profile source, §3.9).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import entity_vies_checks


async def record_vies_check(
    conn: AsyncConnection,
    *,
    entity_id: uuid.UUID,
    country_code: str,
    national_number: str,
    normalized_eu_vat: str,
    valid: bool | None,
    response_hash: str,
    checked_at: datetime,
) -> uuid.UUID:
    check_id = uuid.uuid4()
    await conn.execute(
        entity_vies_checks.insert().values(
            id=check_id,
            entity_id=entity_id,
            country_code=country_code,
            national_number=national_number,
            normalized_eu_vat=normalized_eu_vat,
            checked_at=checked_at,
            vies_valid=valid,
            vies_response_hash=response_hash,
        )
    )
    return check_id

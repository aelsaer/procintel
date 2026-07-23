"""VIES check orchestration — description.txt §3.9.

No caching policy is specified for VIES (unlike ΓΕΜΗ's explicit §18.3
refresh policy) — a check happens whenever called, triggered from
`connectors/ted`'s foreign-supplier detection (a TED supplier with
`country_code != 'GR'`). Every call writes one new `entity_vies_checks` row
— a validation history, not a cache to gate against.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from .client import ViesClient
from .db_writer import record_vies_check


async def check_and_record_vies(
    conn: AsyncConnection,
    *,
    client: ViesClient,
    entity_id: UUID,
    country_code: str,
    vat_number: str,
) -> bool | None:
    """Returns the validity result, or None if the response couldn't be
    parsed — a genuinely unknown outcome, not a failure to hide."""
    response = await client.check_vat(country_code=country_code, vat_number=vat_number)
    response_hash = hashlib.sha256(response.raw_body).hexdigest()
    vat_digits = "".join(ch for ch in vat_number if ch.isalnum()).upper()

    await record_vies_check(
        conn,
        entity_id=entity_id,
        country_code=country_code,
        national_number=vat_digits,
        normalized_eu_vat=f"{country_code}{vat_digits}",
        valid=response.valid,
        response_hash=response_hash,
        checked_at=datetime.now(timezone.utc),
    )
    await conn.commit()
    return response.valid

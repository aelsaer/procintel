"""Idempotent canonical writes for ΜΕΦ expenses.

Dedup on content_sha256 in `source_records`, same shape as every other
connector. `mef_organizations` and `mef_expenses` are otherwise append-only
here — this module stores the raw expense fact; matching it to a specific
ΚΗΜΔΗΣ act (the confidence-tiered logic in §20.2) is `resolve.py`'s job, not
this one's, so `linked_act_id`/`link_method`/`confidence` are left NULL on
insert and updated by `resolve.py` afterward.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import mef_expenses, mef_organizations, source_records

from .normalize import NormalizedMefExpense, normalize_expense_record


@dataclass(frozen=True)
class MefExpenseIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    expense_id: uuid.UUID | None
    mef_organization_id: uuid.UUID | None
    recipient_afm: str | None
    related_ada: str | None
    amount: Any  # Decimal | None
    expense_date: Any  # date | None


async def _find_or_create_organization(
    conn: AsyncConnection, *, normalized: NormalizedMefExpense, source_record_id: uuid.UUID
) -> uuid.UUID:
    if normalized.organization_source_native_id:
        existing = (
            await conn.execute(
                select(mef_organizations.c.id).where(
                    mef_organizations.c.source_native_id == normalized.organization_source_native_id
                )
            )
        ).first()
        if existing is not None:
            return existing.id
    elif normalized.organization_afm:
        existing = (
            await conn.execute(
                select(mef_organizations.c.id).where(mef_organizations.c.afm_raw == normalized.organization_afm)
            )
        ).first()
        if existing is not None:
            return existing.id

    org_id = uuid.uuid4()
    await conn.execute(
        mef_organizations.insert().values(
            id=org_id,
            source_native_id=normalized.organization_source_native_id,
            name=normalized.organization_name,
            afm_raw=normalized.organization_afm,
            source_record_id=source_record_id,
        )
    )
    return org_id


async def ingest_expense_record(
    conn: AsyncConnection,
    *,
    recipient_afm: str,
    raw_body: dict[str, Any],
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> MefExpenseIngestResult:
    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "MEF",
                source_records.c.resource_type == "expense",
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_seen is not None:
        return MefExpenseIngestResult(
            source_record_id=None,
            expense_id=None,
            mef_organization_id=None,
            recipient_afm=None,
            related_ada=None,
            amount=None,
            expense_date=None,
        )

    normalized = normalize_expense_record(raw_body)
    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="MEF",
            resource_type="expense",
            source_native_id=recipient_afm,
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code="CC-BY-4.0",  # TODO(confirm): ΜΕΦ's exact reuse terms
            parse_status="PARSED",
        )
    )

    org_id = await _find_or_create_organization(conn, normalized=normalized, source_record_id=source_record_id)

    expense_id = uuid.uuid4()
    await conn.execute(
        mef_expenses.insert().values(
            id=expense_id,
            mef_organization_id=org_id,
            recipient_afm_raw=normalized.recipient_afm or recipient_afm,
            amount=normalized.amount,
            vat_amount=normalized.vat_amount,
            expense_date=normalized.expense_date,
            related_ada_raw=normalized.related_ada,
            source_record_id=source_record_id,
        )
    )

    return MefExpenseIngestResult(
        source_record_id=source_record_id,
        expense_id=expense_id,
        mef_organization_id=org_id,
        recipient_afm=normalized.recipient_afm or recipient_afm,
        related_ada=normalized.related_ada,
        amount=normalized.amount,
        expense_date=normalized.expense_date,
    )

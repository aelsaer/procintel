"""ΜΕΦ `/api/spendings` record -> canonical shape (spec §3.5, §20)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    for parser in (
        lambda: datetime.strptime(raw, "%d/%m/%Y").date(),
        lambda: date.fromisoformat(raw[:10]),
    ):
        try:
            return parser()
        except ValueError:
            continue
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


class NormalizedMefExpense(BaseModel):
    organization_source_native_id: str | None = None
    organization_name: str | None = None
    organization_afm: str | None = None
    recipient_afm: str | None = None
    recipient_name: str | None = None
    amount: Decimal | None = None
    vat_amount: Decimal | None = None
    expense_date: date | None = None
    related_ada: str | None = None


def normalize_expense_record(raw: dict[str, Any]) -> NormalizedMefExpense:
    organization = raw.get("organization") or {}
    return NormalizedMefExpense(
        organization_source_native_id=raw.get("org.uid") or raw.get("org_uid") or organization.get("id") or raw.get("organizationId"),
        organization_name=raw.get("org.title") or organization.get("name") or raw.get("organizationName"),
        organization_afm=raw.get("org.afm") or organization.get("vatNumber") or raw.get("organizationAfm"),
        recipient_afm=raw.get("issuer_afm") or raw.get("recipientAfm") or raw.get("recipientVatNumber"),
        recipient_name=raw.get("issuer_title") or raw.get("recipientName"),
        amount=_to_decimal(raw.get("amount")),
        vat_amount=_to_decimal(raw.get("vat") if "vat" in raw else raw.get("vatAmount")),
        expense_date=_to_date(raw.get("date") or raw.get("expenseDate")),
        related_ada=raw.get("adas") or raw.get("relatedAda") or raw.get("ada"),
    )

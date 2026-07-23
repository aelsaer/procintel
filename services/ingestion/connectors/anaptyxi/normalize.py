"""ΑΝΑΠΤΥΞΗ project record -> canonical shape (spec §3.4, §19).

Field names below are a best-effort guess — description.txt lists what the
2014-2020 Open Data API exposes conceptually (project, subproject,
beneficiary, contractor, budget, payment) but not the JSON field names,
since no sample payload was available. Fix here once a real payload is
confirmed (docs/source-contracts/anaptyxi.md, Στάδιο 0).

Per-payment detail is deliberately not modeled as individual rows in this
pass (§19.4's known simplification, already documented in the source
mapping) — `contracted_amount`/`paid_amount` are the aggregates the API is
assumed to expose directly.
"""

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
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class NormalizedFundingProject(BaseModel):
    mis_ops_code: str
    program_code: str | None = None
    title: str
    beneficiary_afm: str | None = None
    beneficiary_name: str | None = None
    budget: Decimal | None = None
    contracted_amount: Decimal | None = None
    paid_amount: Decimal | None = None
    currency: str = "EUR"
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None


def normalize_project_record(raw: dict[str, Any], *, mis_code: str) -> NormalizedFundingProject:
    beneficiary = raw.get("beneficiary") or {}
    return NormalizedFundingProject(
        mis_ops_code=mis_code,
        program_code=raw.get("programCode") or raw.get("epCode"),
        title=raw.get("title") or raw.get("projectTitle") or f"ΑΝΑΠΤΥΞΗ project {mis_code}",
        beneficiary_afm=beneficiary.get("vatNumber") or raw.get("beneficiaryVatNumber"),
        beneficiary_name=beneficiary.get("name") or raw.get("beneficiaryName"),
        budget=_to_decimal(raw.get("budget") or raw.get("totalPublicExpenditure")),
        contracted_amount=_to_decimal(raw.get("contractedAmount")),
        paid_amount=_to_decimal(raw.get("paidAmount")),
        currency=raw.get("currency") or "EUR",
        start_date=_to_date(raw.get("startDate")),
        end_date=_to_date(raw.get("endDate")),
        status=raw.get("statusLabel") or raw.get("status"),
    )

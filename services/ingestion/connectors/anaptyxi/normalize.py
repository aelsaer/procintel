"""Normalize official ΑΝΑΠΤΥΞΗ project and subproject payloads."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for format_string in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], format_string).date()
        except ValueError:
            continue
    return None


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("πμ", "AM").replace("μμ", "PM")
    for format_string in (
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, format_string)
        except ValueError:
            continue
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _body_by_category(raw: dict[str, Any], *categories: str) -> dict[str, Any] | None:
    expected = {category.casefold() for category in categories}
    for body in raw.get("bodies") or []:
        if not isinstance(body, dict):
            continue
        category = str(body.get("bodyCategory") or "").casefold()
        if category in expected:
            return body
    return None


class NormalizedFundingSubproject(BaseModel):
    subproject_index: int
    title: str
    implementors: Any | None = None
    budget: Decimal | None = None
    paid_amount: Decimal | None = None
    completion: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None
    subproject_type: str | None = None
    is_grant: bool | None = None
    estimated_status: dict[str, Any] = Field(default_factory=dict)
    actual_status: dict[str, Any] = Field(default_factory=dict)
    bodies: list[dict[str, Any]] = Field(default_factory=list)
    files: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class NormalizedFundingProject(BaseModel):
    mis_ops_code: str
    program_code: str | None = None
    program_title: str | None = None
    title: str
    beneficiary_afm: str | None = None
    beneficiary_name: str | None = None
    budget: Decimal | None = None
    total_budget: Decimal | None = None
    contracted_amount: Decimal | None = None
    paid_amount: Decimal | None = None
    completion: Decimal | None = None
    absorption: Decimal | None = None
    currency: str = "EUR"
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None
    status_report: str | None = None
    status_report_date: datetime | None = None
    description: str | None = None
    is_state_aid: bool | None = None
    is_major: bool | None = None
    funds: str | None = None
    spatial: str | None = None
    thematic: str | None = None
    map_kml: str | None = None
    subprojects: list[NormalizedFundingSubproject] = Field(default_factory=list)
    bodies: list[dict[str, Any]] = Field(default_factory=list)
    geographic_allocations: list[dict[str, Any]] = Field(default_factory=list)
    files: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


def normalize_subproject_record(
    raw: dict[str, Any],
    *,
    subproject_index: int,
) -> NormalizedFundingSubproject:
    return NormalizedFundingSubproject(
        subproject_index=int(raw.get("index") or subproject_index),
        title=str(raw.get("title") or f"Υποέργο {subproject_index}"),
        implementors=raw.get("implementors"),
        budget=_to_decimal(raw.get("budget") or raw.get("totalBudget")),
        paid_amount=_to_decimal(raw.get("payments")),
        completion=_to_decimal(raw.get("completion")),
        start_date=_to_date(raw.get("startDate")),
        end_date=_to_date(raw.get("endDate")),
        subproject_type=str(raw.get("eidos")) if raw.get("eidos") is not None else None,
        is_grant=raw.get("isGrant"),
        estimated_status=raw.get("estimatedStatus") or raw.get("EstimatedStatus") or {},
        actual_status=raw.get("actualStatus") or raw.get("ActualStatus") or {},
        bodies=[item for item in raw.get("bodies") or [] if isinstance(item, dict)],
        files=[item for item in raw.get("files") or [] if isinstance(item, dict)],
        details={
            key: raw.get(key)
            for key in ("description", "foreas", "body", "funds", "projectTitle")
            if raw.get(key) is not None
        },
    )


def normalize_project_record(raw: dict[str, Any], *, mis_code: str) -> NormalizedFundingProject:
    beneficiary = _body_by_category(raw, "ΔΙΚΑΙΟΥΧΟΣ", "BENEFICIARY") or (
        raw.get("beneficiary") if isinstance(raw.get("beneficiary"), dict) else None
    )
    beneficiary_afm = None
    if beneficiary:
        beneficiary_afm = (
            beneficiary.get("afm")
            or beneficiary.get("vatNumber")
            or beneficiary.get("code")
            or raw.get("beneficiaryVatNumber")
        )
    subprojects = [
        normalize_subproject_record(item, subproject_index=index)
        for index, item in enumerate(raw.get("subprojects") or [], start=1)
        if isinstance(item, dict)
    ]
    return NormalizedFundingProject(
        mis_ops_code=mis_code,
        program_code=(
            str(raw.get("epCode") or raw.get("programCode"))
            if raw.get("epCode") is not None or raw.get("programCode") is not None
            else None
        ),
        program_title=raw.get("epTitle"),
        title=raw.get("title") or raw.get("projectTitle") or f"ΑΝΑΠΤΥΞΗ project {mis_code}",
        beneficiary_afm=str(beneficiary_afm) if beneficiary_afm else None,
        beneficiary_name=(beneficiary or {}).get("name") or raw.get("body"),
        budget=_to_decimal(raw.get("budget") or raw.get("totalPublicExpenditure")),
        total_budget=_to_decimal(raw.get("totalBudget")),
        contracted_amount=_to_decimal(
            raw.get("contracts") or raw.get("contractedAmount") or raw.get("budget")
        ),
        paid_amount=_to_decimal(raw.get("payments") or raw.get("paidAmount")),
        completion=_to_decimal(raw.get("completion")),
        absorption=_to_decimal(raw.get("absorption")),
        start_date=_to_date(raw.get("startDate")),
        end_date=_to_date(raw.get("endDate")),
        status=(
            str(raw.get("statusCode"))
            if raw.get("statusCode") is not None
            else raw.get("statusLabel") or raw.get("status")
        ),
        status_report=raw.get("statusReport"),
        status_report_date=_to_datetime(raw.get("statusReportDate")),
        description=raw.get("description"),
        is_state_aid=raw.get("isStateAid"),
        is_major=raw.get("isMajor"),
        funds=raw.get("funds"),
        spatial=raw.get("spatial"),
        thematic=raw.get("thematics"),
        map_kml=raw.get("map"),
        subprojects=subprojects,
        bodies=[item for item in raw.get("bodies") or [] if isinstance(item, dict)],
        geographic_allocations=[
            item for item in raw.get("geoamounts") or [] if isinstance(item, dict)
        ],
        files=[item for item in raw.get("files") or [] if isinstance(item, dict)],
        details={
            "indicators": raw.get("indicators") or [],
            "intervention_fields": raw.get("ifields") or [],
        },
    )

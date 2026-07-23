"""ΓΕΜΗ Open Data v1 company record -> canonical shape (spec §3.3, §18).

`legal_form_code`/`company_status` are normalized against
`lexicon.py`'s canonical vocabulary (not passed through raw) — `legal_form`
keeps the original human-readable label for display, but the *_code/status
fields used for change-detection (`db_writer.py`'s material-field diff)
and cache policy (`cache.py`'s stable-status check) use the canonical
codes so equivalent raw spellings (e.g. "ΕΝΕΡΓΗ" vs "ΕΝ ΕΝΕΡΓΕΙΑ") don't
spuriously look like a material change.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from .lexicon import normalize_company_status, normalize_legal_form_code


class NormalizedCompany(BaseModel):
    afm_raw: str
    afm_normalized: str
    gemi_number: str | None = None
    official_name: str | None = None
    trade_name: str | None = None
    legal_form: str | None = None
    legal_form_code: str | None = None
    company_status: str | None = None
    gemi_office: str | None = None
    gemi_registration_date: date | None = None
    kad_codes: list[str] = []
    municipality: str | None = None
    region: str | None = None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


def _description(value: Any) -> str | None:
    if isinstance(value, dict):
        description = value.get("descr")
        return str(description) if description else None
    return str(value) if value else None


def _activity_codes(raw: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for entry in raw.get("activities") or []:
        if not isinstance(entry, dict):
            continue
        activity = entry.get("activity") or {}
        code = activity.get("id") if isinstance(activity, dict) else None
        if code:
            codes.append(str(code))
    return codes


def normalize_company_record(raw: dict[str, Any], *, afm: str) -> NormalizedCompany:
    raw_afm = str(raw.get("afm") or afm)
    afm_digits = "".join(ch for ch in raw_afm if ch.isdigit())
    titles = _as_list(raw.get("coTitlesEl") or raw.get("tradeName") or raw.get("distinctiveTitle"))
    legal_form = _description(raw.get("legalType")) or raw.get("legalFormLabel") or raw.get("legalForm")
    status = _description(raw.get("status")) or raw.get("statusLabel")
    gemi_office = _description(raw.get("gemiOffice")) or raw.get("competentGemiOffice")
    return NormalizedCompany(
        afm_raw=raw_afm,
        afm_normalized=afm_digits,
        gemi_number=str(raw.get("arGemi") or raw.get("gemiNumber") or raw.get("registryNumber") or "") or None,
        official_name=raw.get("coNameEl") or raw.get("officialName") or raw.get("companyName"),
        trade_name=titles[0] if titles else None,
        legal_form=legal_form,
        legal_form_code=normalize_legal_form_code(legal_form),
        company_status=normalize_company_status(status),
        gemi_office=gemi_office,
        gemi_registration_date=_to_date(raw.get("incorporationDate") or raw.get("registrationDate") or raw.get("gemiRegistrationDate")),
        kad_codes=_activity_codes(raw) or _as_list(raw.get("kadCodes") or raw.get("activityCodes")),
        municipality=_description(raw.get("municipality")) or raw.get("municipalityLabel"),
        region=_description(raw.get("prefecture")) or raw.get("regionLabel"),
    )

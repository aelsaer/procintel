"""TED Search API v3 notice -> canonical shape (spec §21.1-21.2)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel

PARSER_VERSION = "ted-normalize-v1"


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for nested in value.values():
            parsed = _to_decimal(nested)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (list, tuple)):
        for nested in value:
            parsed = _to_decimal(nested)
            if parsed is not None:
                return parsed
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


def _flatten_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        flattened: list[str] = []
        for nested in value.values():
            flattened.extend(_flatten_strings(nested))
        return flattened
    if isinstance(value, (list, tuple)):
        flattened = []
        for nested in value:
            flattened.extend(_flatten_strings(nested))
        return flattened
    return [str(value)]


def _first_text(value: Any) -> str | None:
    if isinstance(value, dict):
        for language in ("ell", "eng"):
            text = _first_text(value.get(language))
            if text:
                return text
    values = _flatten_strings(value)
    return values[0] if values else None


def _country_code(value: Any) -> str | None:
    code = _first_text(value)
    if not code:
        return None
    return {"GRC": "GR"}.get(code.upper(), code.upper()[:2])


def _party_vat(value: Any) -> str | None:
    identifier = _first_text(value)
    if not identifier:
        return None
    digits = "".join(character for character in identifier if character.isdigit())
    return digits if len(digits) >= 8 else identifier


def _detect_eforms_version(raw: dict[str, Any]) -> tuple[str | None, float]:
    """Returns (eforms_version_or_None_for_legacy, parse_confidence).
    UNKNOWN-shaped payloads get a low confidence rather than a guessed
    version — the §21.2 quarantine bucket, expressed as low confidence
    rather than a rejected record (consistent with how the rest of this
    codebase quarantines: keep the data, flag it, don't drop it)."""
    if "eformsVersion" in raw and raw["eformsVersion"]:
        return str(raw["eformsVersion"]), 1.0
    if raw.get("notice-identifier") or raw.get("publication-number"):
        return None, 0.85
    if "legacyFormType" in raw or raw.get("formType") == "legacy":
        return None, 1.0  # legacy forms — no eForms version, by design
    if "notice" in raw or "publicationNumber" in raw:
        # plausible legacy-ish shape but no explicit marker either way
        return None, 0.6
    return None, 0.3  # unrecognized shape — lowest confidence, not a guess


class NormalizedTedParty(BaseModel):
    name: str | None = None
    vat: str | None = None
    country_code: str | None = None


class NormalizedTedNotice(BaseModel):
    ted_notice_id: str
    publication_number: str | None = None
    notice_type: str | None = None
    eforms_version: str | None = None
    parser_version: str = PARSER_VERSION
    parse_confidence: float = 1.0
    title: str | None = None
    buyer: NormalizedTedParty | None = None
    supplier: NormalizedTedParty | None = None
    cpv_codes: list[str] = []
    estimated_value: Decimal | None = None
    awarded_value: Decimal | None = None
    procedure_type: str | None = None
    country_code: str | None = None
    nuts_codes: list[str] = []
    publication_date: date | None = None
    related_notice_ids: list[str] = []


def _normalize_party(raw: dict[str, Any] | None) -> NormalizedTedParty | None:
    if not raw:
        return None
    return NormalizedTedParty(
        name=raw.get("name") or raw.get("officialName"),
        vat=raw.get("vatNumber") or raw.get("vat"),
        country_code=raw.get("countryCode") or raw.get("country"),
    )


def normalize_ted_notice(raw: dict[str, Any], *, ted_notice_id: str) -> NormalizedTedNotice:
    eforms_version, parse_confidence = _detect_eforms_version(raw)
    buyer = _normalize_party(raw.get("buyer")) or NormalizedTedParty(
        name=_first_text(raw.get("buyer-name")),
        vat=_party_vat(raw.get("buyer-identifier")),
        country_code=_country_code(raw.get("buyer-country")),
    )
    supplier = _normalize_party(raw.get("supplier") or raw.get("contractor")) or NormalizedTedParty(
        name=_first_text(raw.get("winner-name")),
        vat=_party_vat(raw.get("winner-identifier")),
        country_code=_country_code(raw.get("winner-country")),
    )
    if not buyer.name and not buyer.vat:
        buyer = None
    if not supplier.name and not supplier.vat:
        supplier = None
    places = _flatten_strings(raw.get("place-of-performance") or raw.get("nutsCodes"))

    return NormalizedTedNotice(
        ted_notice_id=ted_notice_id,
        publication_number=_first_text(raw.get("publication-number")) or raw.get("publicationNumber"),
        notice_type=_first_text(raw.get("notice-type") or raw.get("form-type")) or raw.get("noticeType") or raw.get("formType"),
        eforms_version=eforms_version,
        parse_confidence=parse_confidence,
        title=_first_text(raw.get("notice-title")) or raw.get("title"),
        buyer=buyer,
        supplier=supplier,
        cpv_codes=list(dict.fromkeys(_flatten_strings(raw.get("classification-cpv") or raw.get("cpvCodes") or raw.get("cpv")))),
        estimated_value=_to_decimal(raw.get("estimated-value-proc") or raw.get("estimated-value-lot") or raw.get("estimatedValue")),
        awarded_value=_to_decimal(raw.get("result-value-notice") or raw.get("awardedValue")),
        procedure_type=_first_text(raw.get("procedure-type")) or raw.get("procedureType"),
        country_code=_country_code(raw.get("buyer-country")) or raw.get("countryCode") or raw.get("country"),
        nuts_codes=list(dict.fromkeys(code for code in places if code.upper().startswith("EL") and len(code) >= 4)),
        publication_date=_to_date(raw.get("publication-date") or raw.get("publicationDate")),
        related_notice_ids=_as_list(raw.get("relatedNoticeIds")),
    )


def _element_text(el: ElementTree.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _party_element_to_dict(party_el: ElementTree.Element | None) -> dict[str, Any] | None:
    if party_el is None:
        return None
    return {
        "name": _element_text(party_el.find("Name")),
        "vatNumber": _element_text(party_el.find("VatNumber")),
        "countryCode": _element_text(party_el.find("CountryCode")),
    }


def _notice_element_to_raw_dict(notice_el: ElementTree.Element) -> dict[str, Any]:
    return {
        "noticeId": _element_text(notice_el.find("NoticeId")),
        "publicationNumber": _element_text(notice_el.find("PublicationNumber")),
        "title": _element_text(notice_el.find("Title")),
        "buyer": _party_element_to_dict(notice_el.find("Buyer")),
        "supplier": _party_element_to_dict(notice_el.find("Supplier")),
        "cpvCodes": [t for t in (_element_text(e) for e in notice_el.findall("CpvCodes/CpvCode")) if t],
        "estimatedValue": _element_text(notice_el.find("EstimatedValue")),
        "awardedValue": _element_text(notice_el.find("AwardedValue")),
        "procedureType": _element_text(notice_el.find("ProcedureType")),
        "countryCode": _element_text(notice_el.find("CountryCode")),
        "nutsCodes": [t for t in (_element_text(e) for e in notice_el.findall("NutsCodes/NutsCode")) if t],
        "publicationDate": _element_text(notice_el.find("PublicationDate")),
    }


def parse_bulk_xml_package(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parses a TED bulk-XML export into the same raw-dict shape
    `normalize_ted_notice` already expects from the Search API's JSON —
    bulk XML and Search-API JSON converge on one normalization path, per
    §21.1's "raw XML ή JSON" wording; nothing downstream of this function
    needs to know which source a notice came from.

    Element names (`<Notice>`/`<Buyer>`/`<CpvCodes>`/...) are a best-effort
    guess — no real bulk-export sample was available at build time; confirm
    against the live export before relying on this (docs/source-contracts/
    ted.md). A malformed/unparseable package raises `ElementTree.ParseError`
    rather than silently returning nothing — the caller decides how to
    handle a genuinely broken package, same as an HTTP error elsewhere."""
    root = ElementTree.fromstring(xml_bytes)
    return [_notice_element_to_raw_dict(notice_el) for notice_el in root.findall(".//Notice")]

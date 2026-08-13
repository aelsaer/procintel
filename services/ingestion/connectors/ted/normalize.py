"""TED Search API v3 notice -> canonical shape (spec §21.1-21.2)."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from defusedxml import ElementTree

from pydantic import BaseModel

PARSER_VERSION = "ted-normalize-v2"

_ISO3_TO_ISO2 = {
    "AUT": "AT",
    "BEL": "BE",
    "BGR": "BG",
    "HRV": "HR",
    "CYP": "CY",
    "CZE": "CZ",
    "DEU": "DE",
    "DNK": "DK",
    "EST": "EE",
    "ESP": "ES",
    "FIN": "FI",
    "FRA": "FR",
    "GRC": "GR",
    "HUN": "HU",
    "IRL": "IE",
    "ISL": "IS",
    "ITA": "IT",
    "LIE": "LI",
    "LTU": "LT",
    "LUX": "LU",
    "LVA": "LV",
    "MLT": "MT",
    "NLD": "NL",
    "NOR": "NO",
    "POL": "PL",
    "PRT": "PT",
    "ROU": "RO",
    "SWE": "SE",
    "SVN": "SI",
    "SVK": "SK",
}
_NUTS_CODE = re.compile(r"^[A-Z]{2}[A-Z0-9]{1,3}$")


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
    normalized = code.upper()
    return _ISO3_TO_ISO2.get(normalized, normalized[:2])


def _to_datetime(value: Any, *, fallback_time: time = time(23, 59, 59)) -> datetime | None:
    text = _first_text(value)
    if not text:
        return None
    normalized = text.strip().replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        parsed_date = _to_date(normalized)
        return (
            datetime.combine(parsed_date, fallback_time).replace(tzinfo=timezone.utc)
            if parsed_date is not None
            else None
        )
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed_date = _to_date(normalized)
        if parsed_date is None:
            return None
        parsed = datetime.combine(parsed_date, fallback_time)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _submission_deadline(raw: dict[str, Any]) -> datetime | None:
    direct_values = _flatten_strings(raw.get("deadline-receipt-request"))
    candidates = [
        parsed
        for value in direct_values
        if (parsed := _to_datetime(value)) is not None
    ]

    date_values = (
        _flatten_strings(raw.get("deadline-receipt-tender-date-lot"))
        + _flatten_strings(raw.get("deadline-receipt-request-date-lot"))
    )
    time_values = (
        _flatten_strings(raw.get("deadline-receipt-tender-time-lot"))
        + _flatten_strings(raw.get("deadline-receipt-request-time-lot"))
    )
    for index, date_value in enumerate(date_values):
        deadline_time = time(23, 59, 59)
        if index < len(time_values):
            try:
                deadline_time = time.fromisoformat(time_values[index].strip().replace("Z", "+00:00"))
            except ValueError:
                pass
        parsed = _to_datetime(date_value, fallback_time=deadline_time)
        if parsed is not None:
            candidates.append(parsed)
    return min(candidates) if candidates else None


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
    customization_id = _first_text(
        raw.get("customization-id")
        or raw.get("customizationId")
        or raw.get("CustomizationID")
    )
    if customization_id and "eforms-sdk-" in customization_id.casefold():
        return customization_id.casefold().split("eforms-sdk-", 1)[1], 1.0
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
    submission_deadline: datetime | None = None
    related_notice_ids: list[str] = []
    procedure_identifier: str | None = None
    notice_version: str | None = None
    sdk_customization_id: str | None = None
    previous_notice_ids: list[str] = []
    change_notice_version_identifier: str | None = None


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
    previous_notice_ids = list(
        dict.fromkeys(
            _flatten_strings(raw.get("previous-notice-id-proc"))
            + _flatten_strings(raw.get("modification-previous-notice-identifier"))
            + _flatten_strings(raw.get("relatedNoticeIds"))
        )
    )
    customization_id = _first_text(
        raw.get("customization-id")
        or raw.get("customizationId")
        or raw.get("CustomizationID")
    )

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
        nuts_codes=list(
            dict.fromkeys(
                normalized
                for code in places
                if (normalized := code.strip().upper())
                and _NUTS_CODE.fullmatch(normalized)
            )
        ),
        publication_date=_to_date(raw.get("publication-date") or raw.get("publicationDate")),
        submission_deadline=_submission_deadline(raw),
        related_notice_ids=previous_notice_ids,
        procedure_identifier=_first_text(raw.get("procedure-identifier"))
        or raw.get("procedureIdentifier"),
        notice_version=_first_text(raw.get("notice-version"))
        or raw.get("noticeVersion"),
        sdk_customization_id=customization_id,
        previous_notice_ids=previous_notice_ids,
        change_notice_version_identifier=_first_text(
            raw.get("change-notice-version-identifier")
        )
        or raw.get("changeNoticeVersionIdentifier"),
    )


def _element_text(el: ElementTree.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].split(":")[-1]


def _direct(element: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    expected = set(names)
    return next((child for child in element if _local_name(child) in expected), None)


def _descendant(element: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    expected = set(names)
    return next((child for child in element.iter() if _local_name(child) in expected), None)


def _descendant_text(element: ElementTree.Element, *names: str) -> str | None:
    return _element_text(_descendant(element, *names))


def _descendant_texts(element: ElementTree.Element, *names: str) -> list[str]:
    expected = set(names)
    return [
        text
        for child in element.iter()
        if _local_name(child) in expected
        if (text := _element_text(child))
    ]


def _party_element_to_dict(party_el: ElementTree.Element | None) -> dict[str, Any] | None:
    if party_el is None:
        return None
    return {
        "name": _element_text(_direct(party_el, "Name"))
        or _descendant_text(party_el, "Name", "RegistrationName"),
        "vatNumber": _element_text(_direct(party_el, "VatNumber"))
        or _descendant_text(party_el, "CompanyID", "EndpointID"),
        "countryCode": _element_text(_direct(party_el, "CountryCode"))
        or _descendant_text(party_el, "IdentificationCode", "CountrySubentityCode"),
    }


def _notice_element_to_raw_dict(notice_el: ElementTree.Element) -> dict[str, Any]:
    buyer_element = _direct(notice_el, "Buyer")
    if buyer_element is None:
        buyer_element = _descendant(notice_el, "ContractingParty")
    supplier_element = _direct(notice_el, "Supplier")
    if supplier_element is None:
        supplier_element = _descendant(
            notice_el, "WinningParty", "TendererParty", "EconomicOperatorParty"
        )
    notice_id = (
        _element_text(_direct(notice_el, "NoticeId", "NoticeID"))
        or _element_text(_direct(notice_el, "ID"))
        or _descendant_text(notice_el, "NoticeIdentifier")
    )
    project_element = _descendant(notice_el, "ProcurementProject")
    project_title = (
        _element_text(_direct(project_element, "Name", "Title"))
        or _descendant_text(project_element, "Name", "Title", "Description")
        if project_element is not None
        else None
    )
    return {
        "noticeId": notice_id,
        "notice-identifier": notice_id,
        "publicationNumber": _element_text(_direct(notice_el, "PublicationNumber"))
        or _descendant_text(notice_el, "PublicationNumber"),
        "title": _element_text(_direct(notice_el, "Title"))
        or project_title
        or _descendant_text(notice_el, "Name", "Description"),
        "buyer": _party_element_to_dict(buyer_element),
        "supplier": _party_element_to_dict(supplier_element),
        "cpvCodes": list(
            dict.fromkeys(_descendant_texts(notice_el, "CpvCode", "ItemClassificationCode"))
        ),
        "estimatedValue": _descendant_text(
            notice_el,
            "EstimatedValue",
            "EstimatedOverallContractAmount",
            "EstimatedOverallContractAmountValue",
        ),
        "awardedValue": _descendant_text(
            notice_el,
            "AwardedValue",
            "PayableAmount",
            "TaxExclusiveAmount",
            "TotalAmount",
        ),
        "procedureType": _descendant_text(notice_el, "ProcedureType", "ProcedureCode"),
        "countryCode": _element_text(_direct(notice_el, "CountryCode"))
        or _descendant_text(notice_el, "IdentificationCode"),
        "nutsCodes": list(
            dict.fromkeys(
                _descendant_texts(
                    notice_el,
                    "NutsCode",
                    "CountrySubentityCode",
                    "Region",
                )
            )
        ),
        "publicationDate": _element_text(_direct(notice_el, "PublicationDate"))
        or _element_text(_direct(notice_el, "IssueDate"))
        or _descendant_text(notice_el, "PublicationDate"),
        "customization-id": _element_text(_direct(notice_el, "CustomizationID"))
        or _descendant_text(notice_el, "CustomizationID"),
        "notice-version": _descendant_text(
            notice_el,
            "NoticeVersion",
            "NoticeVersionCode",
        ),
        "procedure-identifier": _descendant_text(
            notice_el,
            "ProcedureIdentifier",
            "ProcedureID",
        ),
        "previous-notice-id-proc": _descendant_texts(
            notice_el,
            "PreviousNoticeID",
            "PreviousNoticeIdentifier",
            "ReferencedNoticeID",
        ),
        "change-notice-version-identifier": _descendant_text(
            notice_el,
            "ChangeNoticeVersionIdentifier",
        ),
    }


def parse_bulk_xml_package(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parses a TED bulk-XML export into the same raw-dict shape
    `normalize_ted_notice` already expects from the Search API's JSON —
    bulk XML and Search-API JSON converge on one normalization path, per
    §21.1's "raw XML ή JSON" wording; nothing downstream of this function
    needs to know which source a notice came from.

    The parser is namespace-agnostic and supports both the compact operator
    package shape and UBL/eForms notice roots. A malformed package raises
    ``ElementTree.ParseError`` rather than silently returning no records."""
    root = ElementTree.fromstring(xml_bytes)
    notice_names = {
        "Notice",
        "ContractNotice",
        "ContractAwardNotice",
        "PriorInformationNotice",
        "QualificationSystemNotice",
        "BusinessRegistrationInformationNotice",
        "ContractModificationNotice",
    }
    notice_elements = [
        element for element in root.iter() if _local_name(element) in notice_names
    ]
    return [_notice_element_to_raw_dict(notice_el) for notice_el in notice_elements]

"""ΚΗΜΔΗΣ record -> canonical shape, shared across all five resources.

description.txt §3.1/§16 gives its field-preservation list once, generically
("Το ΚΗΜΔΗΣ πρέπει να υποστηριχθεί για όλους τους βασικούς πόρους ... Τα
πεδία που πρέπει να διατηρούνται αυτούσια περιλαμβάνουν ..."), immediately
after listing all five endpoints — so it's read here as the common field set
for request/notice/auction/contract/payment, not contract-specific. The
resource-specific mappings are validated against live request, notice,
auction, contract and payment payloads. Legacy aliases remain supported so
older archived records can be replayed through the same normalizer.

Also implements: casing-drift-tolerant field lookup (`contractRelatedAda` /
`contractRelatedADA`), ΑΦΜ checksum validation (identifier kept either way,
§7.2), CPV/NUTS list handling, and the two funding-reference fields kept
separate and unverified (§19.4).

`end_date` maps the live contract `endDate` field and retains the historical
`contractEndDate`/`contractDurationEndDate` aliases.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from .afm import valid_greek_afm

KhmdhsActType = str  # REQUEST | NOTICE | AWARD | CONTRACT | PAYMENT

_ACT_TYPE_BY_RESOURCE: dict[str, KhmdhsActType] = {
    "request": "REQUEST",
    "notice": "NOTICE",
    "auction": "AWARD",
    "contract": "CONTRACT",
    "payment": "PAYMENT",
}

_EXTRA_DATE_KEYS: dict[str, tuple[str, ...]] = {
    "payment": ("signedDate", "paymentDate"),
}
_EXTRA_AMOUNT_GROSS_KEYS: dict[str, tuple[str, ...]] = {
    "payment": ("contractValue", "paymentAmountWithVAT", "paymentAmount"),
}


def _first(raw: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-None value among case/spelling variants
    of the same logical field (handles ΚΗΜΔΗΣ's documented casing drift,
    e.g. contractRelatedAda vs contractRelatedADA)."""
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _key_value(value: Any, *, prefer: str = "key") -> Any:
    if not isinstance(value, dict):
        return value
    first_key = prefer
    second_key = "value" if prefer == "key" else "key"
    direct_value = value.get(first_key) or value.get(second_key)
    if direct_value:
        return direct_value
    if len(value) == 1:
        nested = next(iter(value.values()))
        if isinstance(nested, dict):
            return _key_value(nested, prefer=prefer)
    return None


def _key_value_str(value: Any, *, prefer: str = "key") -> str | None:
    """Same as `_key_value`, coerced to `str` (or `None`) — pydantic v2's
    `str` fields don't auto-stringify the way v1's did, so any leftover
    non-string leaf (a numeric code where a text label was expected, a
    stray nested shape `_key_value` didn't unwrap) must be coerced
    explicitly or `NormalizedAct` construction raises, same failure mode
    that motivated adding this in the first place (§procedure_type coming
    back as a raw `{key, value}` object instead of a string)."""
    resolved = _key_value(value, prefer=prefer)
    return str(resolved) if resolved is not None else None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        resolved = _key_value(value)
        return [str(resolved)] if resolved else []
    if isinstance(value, (list, tuple)):
        resolved_values = [_key_value(v) for v in value]
        return [str(v) for v in resolved_values if v]
    return [str(value)]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _object_details(raw: dict[str, Any]) -> list[dict[str, Any]]:
    details = raw.get("objectDetailsList") or raw.get("objectDetails")
    return [item for item in details if isinstance(item, dict)] if isinstance(details, list) else []


def _cpv_codes(raw: dict[str, Any]) -> list[str]:
    explicit = _as_list(raw.get("cpvItems"))
    if explicit:
        return _dedupe(explicit)
    codes: list[str] = []
    for detail in _object_details(raw):
        codes.extend(_as_list(detail.get("cpvs")))
    return _dedupe(codes)


def _nuts_codes(raw: dict[str, Any]) -> list[str]:
    # Older ΚΗΜΔΗΣ records can carry both a precise top-level `nutsCode`
    # object and a broader `nutsCodes` list shaped as `{nutsCode: {key, value}}`.
    # Keep both; the UI can then show the available geographic coverage.
    return _dedupe(_as_list(raw.get("nutsCode")) + _as_list(raw.get("nutsCodes")))


def _first_object_detail(raw: dict[str, Any]) -> dict[str, Any] | None:
    details = _object_details(raw)
    return details[0] if details else None


def _object_details_net_total(raw: dict[str, Any]) -> Decimal | None:
    values = [
        value
        for item in _object_details(raw)
        if (value := _to_decimal(item.get("costWithoutVAT"))) is not None
    ]
    return sum(values, Decimal("0")) if values else None


def _source_details(raw: dict[str, Any]) -> dict[str, Any]:
    first_detail = _first_object_detail(raw) or {}
    object_details = []
    for item in _object_details(raw):
        object_details.append({
            "short_description": item.get("shortDescription"),
            "cpv_codes": _as_list(item.get("cpvs")),
            "cost_without_vat": item.get("costWithoutVAT"),
            "quantity": item.get("quantity"),
            "unit": _key_value_str(item.get("type"), prefer="value"),
        })
    return {
        "notice_type": _key_value_str(raw.get("noticeType"), prefer="value"),
        "contract_type": _key_value_str(raw.get("contractType"), prefer="value"),
        "procedure_type": (
            _key_value_str(raw.get("typeOfProcedure"), prefer="value")
            or _key_value_str(raw.get("procedureType"), prefer="value")
            or _key_value_str(raw.get("procedureCategory"), prefer="value")
        ),
        "award_criterion": _key_value_str(raw.get("criteriaCode"), prefer="value"),
        "legal_context": _key_value_str(raw.get("legalContext"), prefer="value"),
        "bidding_website": raw.get("biddingWebsite"),
        "systemic_numbers": [
            str(value)
            for item in (raw.get("systemicNumbers") or [])
            if isinstance(item, dict)
            and (value := item.get("systemicNumber") or item.get("number"))
        ],
        "duration": raw.get("contractDuration"),
        "duration_unit": _key_value_str(raw.get("contractDurationUnitOfMeasure"), prefer="value"),
        "city": raw.get("nutsCity") or first_detail.get("city"),
        "postal_code": raw.get("nutsPostalCode") or first_detail.get("postalCode"),
        "object_details": object_details,
    }


def normalize_adam(value: str) -> str:
    """Shared ΑΔΑΜ canonicalization (§7.2: trim whitespace, uppercase, never
    fuzzy-matched) — reused by db_writer.py and adamchain.py so the exact
    same string always resolves to the exact same identifier."""
    return value.strip().upper()


class NormalizedParty(BaseModel):
    afm_raw: str | None = None
    afm_normalized: str | None = None
    afm_checksum_valid: bool = False
    source_native_id: str | None = None
    name: str | None = None
    amount: Decimal | None = None


class NormalizedAct(BaseModel):
    act_type: KhmdhsActType
    source_native_id: str  # referenceNumber (ADAM)
    adam_normalized: str
    title: str | None = None
    submission_date: date | None = None
    publication_date: date | None = None
    submission_deadline: datetime | None = None
    end_date: date | None = None
    procedure_type: str | None = None
    amount_net: Decimal | None = None
    vat_amount: Decimal | None = None
    amount_gross: Decimal | None = None
    currency: str = "EUR"
    cpv_codes: list[str] = []
    nuts_codes: list[str] = []
    buyer: NormalizedParty | None = None
    contractor: NormalizedParty | None = None
    contractors: list[NormalizedParty] = Field(default_factory=list)
    related_ada: list[str] = []  # decisionRelatedAda / contractRelatedAda(ADA) / cancellationADA
    commitment_no: str | None = None
    aaht_raw: str | None = None
    public_funding_ref_ops: str | None = None  # candidate MIS join key, unverified (§19.4)
    espa_fund_program_ref: str | None = None  # candidate MIS join key, unverified (§19.4)
    source_details: dict[str, Any] = Field(default_factory=dict)


# Backward-compat alias: earlier code/tests referred to the contract-only name.
NormalizedContractAct = NormalizedAct


def _normalize_party(
    afm_raw: Any,
    name: Any,
    amount: Any,
    *,
    source_native_id: Any = None,
) -> NormalizedParty | None:
    if not afm_raw and not source_native_id:
        return None
    afm_str = str(afm_raw) if afm_raw else None
    digits_only = "".join(ch for ch in afm_str if ch.isdigit()) if afm_str else None
    return NormalizedParty(
        afm_raw=afm_str,
        afm_normalized=digits_only,
        afm_checksum_valid=valid_greek_afm(afm_str) if afm_str else False,
        source_native_id=str(source_native_id) if source_native_id else None,
        name=str(name) if name else None,
        amount=_to_decimal(amount),
    )


def _contractor_payloads(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every supplier or consortium member across live and legacy shapes."""
    candidates: list[dict[str, Any]] = []
    legacy = raw.get("awardees") or raw.get("contractors")
    if isinstance(legacy, list):
        candidates.extend(item for item in legacy if isinstance(item, dict))
    elif isinstance(legacy, dict):
        candidates.append(legacy)

    contracting_details = raw.get("contractingDataDetails")
    if isinstance(contracting_details, dict):
        members = contracting_details.get("contractingMembersDataList") or []
        if isinstance(members, list):
            candidates.extend(item for item in members if isinstance(item, dict))

    # Payment records expose the payee in objectDetails.
    candidates.extend(
        detail
        for detail in _object_details(raw)
        if detail.get("vatNo") or detail.get("vatNumber") or detail.get("afm")
    )
    return candidates


def normalize_khmdhs_record(raw: dict[str, Any], *, resource: str) -> NormalizedAct:
    if resource not in _ACT_TYPE_BY_RESOURCE:
        raise ValueError(f"unknown KHMDHS resource: {resource!r}")
    act_type = _ACT_TYPE_BY_RESOURCE[resource]

    adam_raw = raw["referenceNumber"]
    adam_normalized = normalize_adam(str(adam_raw))

    funding_details = raw.get("fundingDetails") if isinstance(raw.get("fundingDetails"), dict) else {}
    first_object_detail = _first_object_detail(raw)

    related_ada = _dedupe([
        str(v).strip().upper()
        for value in (
            _first(raw, "decisionRelatedAda", "decisionRelatedADA"),
            _first(raw, "contractRelatedAda", "contractRelatedADA"),
            _first(raw, "approvalADA", "approvalAda"),
            _first(raw, "cancellationADA", "cancellationAda"),
            _first(raw, "paymentRelatedAda", "paymentRelatedADA"),
            _first(raw, "diavgeiaADA", "diavgeiaAda"),
        )
        for v in _as_list(value)
        if v
    ])

    date_value = _first(
        raw,
        *(_EXTRA_DATE_KEYS.get(resource, ()) if resource == "payment" else ("submissionDate",)),
    )
    amount_net = _to_decimal(
        raw.get("totalCostWithoutVAT")
        or raw.get("amountNet")
    ) or _object_details_net_total(raw)
    if amount_net is None and resource != "payment":
        amount_net = _to_decimal(
            raw.get("contractValue") or (first_object_detail or {}).get("costWithoutVAT")
        )
    amount_gross_raw = (
        raw.get("totalCostWithVAT")
        or raw.get("amountGross")
        or raw.get("totalCost")
        or _first(raw, *_EXTRA_AMOUNT_GROSS_KEYS.get(resource, ()))
    )
    amount_gross = _to_decimal(amount_gross_raw)
    vat_amount = _to_decimal(raw.get("vatAmount"))
    if vat_amount is None and amount_net is not None and amount_gross is not None:
        vat_amount = amount_gross - amount_net

    contractor_parties: list[NormalizedParty] = []
    seen_contractors: set[tuple[str | None, str | None, str | None]] = set()
    for payload in _contractor_payloads(raw):
        party = _normalize_party(
            payload.get("vatNo") or payload.get("vatNumber") or payload.get("afm"),
            payload.get("name") or payload.get("companyName"),
            payload.get("amount") or amount_gross_raw or amount_net,
            source_native_id=payload.get("id") or payload.get("gemiNumber"),
        )
        if party is None:
            continue
        identity = (party.afm_normalized, party.source_native_id, party.name)
        if identity in seen_contractors:
            continue
        seen_contractors.add(identity)
        contractor_parties.append(party)
    primary_contractor = contractor_parties[0] if contractor_parties else None

    return NormalizedAct(
        act_type=act_type,
        source_native_id=str(adam_raw),
        adam_normalized=adam_normalized,
        title=raw.get("title"),
        submission_date=_to_date(date_value),
        publication_date=_to_date(
            _first(raw, "publishedDate", "contractSignedDate", "signedDate", "startDate")
        ),
        submission_deadline=_to_datetime(
            _first(raw, "finalSubmissionDate", "offersSubmissionDeadline", "submissionDeadline")
        ),
        end_date=_to_date(_first(raw, "contractEndDate", "endDate", "contractDurationEndDate")),
        procedure_type=_key_value_str(raw.get("typeOfProcedure"), prefer="value")
        or _key_value_str(raw.get("procedureType"), prefer="value")
        or _key_value_str(raw.get("procedureCategory"), prefer="value"),
        amount_net=amount_net,
        vat_amount=vat_amount,
        amount_gross=amount_gross,
        currency=raw.get("currency") or "EUR",
        cpv_codes=_cpv_codes(raw),
        nuts_codes=_nuts_codes(raw),
        buyer=_normalize_party(
            raw.get("organizationVatNumber"),
            raw.get("organizationName") or _key_value(raw.get("organization"), prefer="value"),
            None,
            source_native_id=_key_value(raw.get("organization"), prefer="key"),
        ),
        contractor=primary_contractor,
        contractors=contractor_parties,
        related_ada=related_ada,
        commitment_no=raw.get("commitmentNo") or raw.get("paymentCommitmentCode"),
        aaht_raw=raw.get("aaht"),
        public_funding_ref_ops=raw.get("publicFundingRefOps") or funding_details.get("publicFundingRefOps"),
        espa_fund_program_ref=raw.get("espaFundProgramRef") or funding_details.get("espaFundProgramRef"),
        source_details=_source_details(raw),
    )


def normalize_contract_record(raw: dict[str, Any]) -> NormalizedAct:
    """Backward-compat wrapper — prefer normalize_khmdhs_record(raw, resource=...)."""
    return normalize_khmdhs_record(raw, resource="contract")


def normalize_request_record(raw: dict[str, Any]) -> NormalizedAct:
    return normalize_khmdhs_record(raw, resource="request")


def normalize_notice_record(raw: dict[str, Any]) -> NormalizedAct:
    return normalize_khmdhs_record(raw, resource="notice")


def normalize_auction_record(raw: dict[str, Any]) -> NormalizedAct:
    return normalize_khmdhs_record(raw, resource="auction")


def normalize_payment_record(raw: dict[str, Any]) -> NormalizedAct:
    return normalize_khmdhs_record(raw, resource="payment")

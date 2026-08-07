"""Regex-based entity/field extraction from document text (§23.3's target
list: ΑΔΑ, ΑΔΑΜ, ΑΦΜ, CPV, MIS/OPS, dates, protocol numbers, duration, lot
numbers, units of measurement, IBAN).

Every extractor here *locates* a token inside free text and reports it with
a confidence — it never claims to be the authoritative value for a field
(§23.5); callers write results to `field_provenance` with
`extraction_method='REGEX'` for a human/downstream process to weigh
against the ΚΗΜΔΗΣ/Διαύγεια source-of-truth records for the same act.

ΑΔΑ/ΑΔΑΜ shape: description.txt §7.2 gives normalization *rules*
(uppercase, trim, never fuzzy-match, ΑΔΑΜ category from
REQ/PROC/AWRD/SYMV/PAY) but no regex/character-count for the "μορφή"
itself — the patterns below are inferred from observed real-world
examples (e.g. ΑΔΑ `7Α1Η465ΦΘΘ-ΘΙΚ`, ΑΔΑΜ `25SYMV012345678`) and kept
deliberately width-tolerant (a range of lengths, not one fixed count)
rather than guessing an exact character count the spec doesn't confirm.
`services/ingestion/connectors/khmdhs/afm.py::valid_greek_afm` is reused
as-is for ΑΦΜ checksum validation — the spec's own reference
implementation, not reinvented here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from services.ingestion.connectors.khmdhs.afm import valid_greek_afm

# ---------------------------------------------------------------------------
# ΑΔΑ / ΑΔΑΜ
# ---------------------------------------------------------------------------

_GREEK_ALNUM = "0-9Α-Ω"
# 6-10 alphanumerics, hyphen, 3-4 Greek letters — see module docstring on why
# this is a tolerant range rather than one confirmed fixed width.
_ADA_RE = re.compile(rf"\b([{_GREEK_ALNUM}]{{6,10}}-[Α-Ω]{{3,4}})\b")
_ADAM_CATEGORIES = ("REQ", "PROC", "AWRD", "SYMV", "PAY")
_ADAM_RE = re.compile(rf"\b(\d{{2}}(?:{'|'.join(_ADAM_CATEGORIES)})\d{{6,10}})\b")


@dataclass(frozen=True)
class ExtractedAda:
    raw_value: str
    normalized_value: str
    span: tuple[int, int]


@dataclass(frozen=True)
class ExtractedAdam:
    raw_value: str
    normalized_value: str
    category: str
    span: tuple[int, int]


def extract_ada(text: str) -> list[ExtractedAda]:
    results = []
    for m in _ADA_RE.finditer(text):
        raw = m.group(1)
        results.append(ExtractedAda(raw_value=raw, normalized_value=raw.strip().upper(), span=m.span(1)))
    return results


def extract_adam(text: str) -> list[ExtractedAdam]:
    results = []
    for m in _ADAM_RE.finditer(text):
        raw = m.group(1)
        normalized = raw.strip().upper()
        category = next(cat for cat in _ADAM_CATEGORIES if cat in normalized)
        results.append(ExtractedAdam(raw_value=raw, normalized_value=normalized, category=category, span=m.span(1)))
    return results


# ---------------------------------------------------------------------------
# ΑΦΜ (§7.2: exactly 9 digits, checksum validation, never rejected outright
# on a failed checksum — identifier_valid/match_eligibility carry that)
# ---------------------------------------------------------------------------

_AFM_LABEL_RE = re.compile(r"(?:ΑΦΜ|Α\.Φ\.Μ\.?|VAT)\s*[:\.]?\s*(\d{9})\b", re.IGNORECASE)
_AFM_BARE_RE = re.compile(r"\b(\d{9})\b")


@dataclass(frozen=True)
class ExtractedAfm:
    raw_value: str
    checksum_valid: bool
    labeled: bool  # True if found right after an "ΑΦΜ:"/"VAT:" label — much higher confidence than a bare 9-digit run
    span: tuple[int, int]


def extract_afm(text: str) -> list[ExtractedAfm]:
    results: list[ExtractedAfm] = []
    seen_spans: set[tuple[int, int]] = set()

    for m in _AFM_LABEL_RE.finditer(text):
        span = m.span(1)
        seen_spans.add(span)
        raw = m.group(1)
        results.append(ExtractedAfm(raw_value=raw, checksum_valid=valid_greek_afm(raw), labeled=True, span=span))

    for m in _AFM_BARE_RE.finditer(text):
        span = m.span(1)
        if span in seen_spans:
            continue
        raw = m.group(1)
        if not valid_greek_afm(raw):
            continue  # unlabeled + fails checksum: too weak a signal to report at all
        results.append(ExtractedAfm(raw_value=raw, checksum_valid=True, labeled=False, span=span))

    results.sort(key=lambda r: r.span[0])
    return results


# ---------------------------------------------------------------------------
# Procurement participants. A bare company name or ΑΦΜ is not sufficient:
# the extractor requires an explicit procurement role and an adjacent ΑΦΜ so
# a result can be linked to a company and audited in the source document.
# ---------------------------------------------------------------------------

_PARTICIPANT_ROLE_RE = re.compile(
    r"(?P<label>"
    r"ΠΡΟΣΩΡΙΝ(?:ΟΣ|Η)\s+ΑΝΑΔΟΧΟΣ|"
    r"ΟΡΙΣΤΙΚ(?:ΟΣ|Η)\s+ΑΝΑΔΟΧΟΣ|"
    r"ΑΝΑΔΟΧΟΣ|ΜΕΙΟΔΟΤΗΣ|"
    r"ΠΡΟΣΦΕΡΩΝ|ΣΥΜΜΕΤΕΧΩΝ|ΥΠΟΨΗΦΙΟΣ|ΟΙΚΟΝΟΜΙΚΟΣ\s+ΦΟΡΕΑΣ|"
    r"ΜΕΛΟΣ\s+(?:ΤΗΣ\s+)?(?:ΕΝΩΣΗΣ|ΚΟΙΝΟΠΡΑΞΙΑΣ)|ΚΟΙΝΟΠΡΑΞΙΑ"
    r")\s*[:\-]?\s*"
    r"(?P<name>[^\n\r;]{2,180}?)"
    r"(?:\s*[,|]\s*|\s+)"
    r"(?:Α\.?\s*Φ\.?\s*Μ\.?|VAT)\s*[:\.]?\s*(?P<afm>\d{9})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedProcurementParticipant:
    name: str
    afm: str
    role: str  # BIDDER | WINNER | CONSORTIUM_MEMBER
    role_label: str
    confidence: float
    checksum_valid: bool
    span: tuple[int, int]


def _participant_role(label: str) -> tuple[str, float]:
    normalized = label.upper()
    if "ΕΝΩΣ" in normalized or "ΚΟΙΝΟΠΡΑΞ" in normalized:
        return "CONSORTIUM_MEMBER", 0.92
    if "ΑΝΑΔΟΧ" in normalized or "ΜΕΙΟΔΟΤ" in normalized:
        return "WINNER", 0.97
    return "BIDDER", 0.93


def extract_procurement_participants(text: str) -> list[ExtractedProcurementParticipant]:
    results: list[ExtractedProcurementParticipant] = []
    seen: set[tuple[str, str, str]] = set()
    for match in _PARTICIPANT_ROLE_RE.finditer(text):
        name = re.sub(r"\s+", " ", match.group("name")).strip(" ,:-|")
        afm = match.group("afm")
        role, confidence = _participant_role(match.group("label"))
        key = (name.upper(), afm, role)
        if not name or key in seen:
            continue
        seen.add(key)
        checksum_valid = valid_greek_afm(afm)
        results.append(
            ExtractedProcurementParticipant(
                name=name,
                afm=afm,
                role=role,
                role_label=match.group("label"),
                confidence=confidence if checksum_valid else round(confidence * 0.62, 4),
                checksum_valid=checksum_valid,
                span=match.span(),
            )
        )
    return results


# ---------------------------------------------------------------------------
# CPV (§7.2: 8-digit base code + check digit where present)
# ---------------------------------------------------------------------------

_CPV_WITH_CHECK_RE = re.compile(r"\b(\d{8})-(\d)\b")
_CPV_LABELED_RE = re.compile(r"CPV\s*[:\.]?\s*(\d{8})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractedCpv:
    raw_value: str
    base_code: str
    check_digit: str | None
    span: tuple[int, int]


def extract_cpv(text: str) -> list[ExtractedCpv]:
    results: list[ExtractedCpv] = []
    seen_spans: set[tuple[int, int]] = set()

    for m in _CPV_WITH_CHECK_RE.finditer(text):
        seen_spans.add(m.span())
        results.append(
            ExtractedCpv(raw_value=m.group(0), base_code=m.group(1), check_digit=m.group(2), span=m.span())
        )

    for m in _CPV_LABELED_RE.finditer(text):
        span = m.span(1)
        if any(span[0] >= s[0] and span[1] <= s[1] for s in seen_spans):
            continue
        results.append(ExtractedCpv(raw_value=m.group(1), base_code=m.group(1), check_digit=None, span=span))

    results.sort(key=lambda r: r.span[0])
    return results


# ---------------------------------------------------------------------------
# MIS/OPS (§7.2: only exact match when the source explicitly labels it as an
# ΟΠΣ code — never inferred from a bare number)
# ---------------------------------------------------------------------------

_MIS_RE = re.compile(r"(?:ΟΠΣ|MIS|Κωδικός\s+ΟΠΣ)\s*[:\.]?\s*(\d{4,8})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractedMis:
    raw_value: str
    span: tuple[int, int]


def extract_mis(text: str) -> list[ExtractedMis]:
    return [ExtractedMis(raw_value=m.group(1), span=m.span(1)) for m in _MIS_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Dates: numeric dd/mm/yyyy or dd-mm-yyyy, and Greek month-name dates
# ---------------------------------------------------------------------------

_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_GREEK_MONTHS = {
    "ιανουαρίου": 1, "φεβρουαρίου": 2, "μαρτίου": 3, "απριλίου": 4,
    "μαΐου": 5, "ιουνίου": 6, "ιουλίου": 7, "αυγούστου": 8,
    "σεπτεμβρίου": 9, "οκτωβρίου": 10, "νοεμβρίου": 11, "δεκεμβρίου": 12,
}
_NORMALIZED_GREEK_MONTHS = {
    unicodedata.normalize("NFC", name).casefold(): number
    for name, number in _GREEK_MONTHS.items()
}
_GREEK_DATE_RE = re.compile(
    rf"\b(\d{{1,2}})\s+({'|'.join(_GREEK_MONTHS)})\s+(\d{{4}})\b", re.IGNORECASE
)


@dataclass(frozen=True)
class ExtractedDate:
    raw_value: str
    year: int
    month: int
    day: int
    span: tuple[int, int]


def extract_dates(text: str) -> list[ExtractedDate]:
    text = unicodedata.normalize("NFC", text)
    results: list[ExtractedDate] = []
    for m in _NUMERIC_DATE_RE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        results.append(ExtractedDate(raw_value=m.group(0), year=year, month=month, day=day, span=m.span()))
    for m in _GREEK_DATE_RE.finditer(text):
        day = int(m.group(1))
        month = _NORMALIZED_GREEK_MONTHS.get(
            unicodedata.normalize("NFC", m.group(2)).casefold()
        )
        if month is None:
            continue
        year = int(m.group(3))
        results.append(ExtractedDate(raw_value=m.group(0), year=year, month=month, day=day, span=m.span()))
    results.sort(key=lambda r: r.span[0])
    return results


# ---------------------------------------------------------------------------
# Protocol numbers
# ---------------------------------------------------------------------------

_PROTOCOL_RE = re.compile(
    r"Αρ(?:ιθ)?\.?\s*Πρωτ(?:οκόλλου)?\.?\s*[:\.]?\s*([Α-Ωa-zA-Z0-9]+(?:/\d{2,4})?)", re.IGNORECASE
)


@dataclass(frozen=True)
class ExtractedProtocolNumber:
    raw_value: str
    span: tuple[int, int]


def extract_protocol_numbers(text: str) -> list[ExtractedProtocolNumber]:
    return [ExtractedProtocolNumber(raw_value=m.group(1), span=m.span(1)) for m in _PROTOCOL_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"\b(\d+)\s*(ημέρ(?:α|ες)|μήν(?:α|ες)|έτ(?:ος|η))\b", re.IGNORECASE
)
_DURATION_UNIT = {"ημέρα": "DAYS", "ημέρες": "DAYS", "μήνα": "MONTHS", "μήνες": "MONTHS", "έτος": "YEARS", "έτη": "YEARS"}


@dataclass(frozen=True)
class ExtractedDuration:
    raw_value: str
    quantity: int
    unit: str  # DAYS | MONTHS | YEARS
    span: tuple[int, int]


def extract_duration(text: str) -> list[ExtractedDuration]:
    results = []
    for m in _DURATION_RE.finditer(text):
        unit = _DURATION_UNIT.get(m.group(2).lower())
        if unit is None:
            continue
        results.append(ExtractedDuration(raw_value=m.group(0), quantity=int(m.group(1)), unit=unit, span=m.span()))
    return results


# ---------------------------------------------------------------------------
# Lot numbers
# ---------------------------------------------------------------------------

_LOT_RE = re.compile(r"(?:Τμήμα|ΤΜΗΜΑ|Lot|LOT)\s*[:\.]?\s*(\d+)\b")


@dataclass(frozen=True)
class ExtractedLotNumber:
    raw_value: str
    lot_number: int
    span: tuple[int, int]


def extract_lot_numbers(text: str) -> list[ExtractedLotNumber]:
    return [
        ExtractedLotNumber(raw_value=m.group(0), lot_number=int(m.group(1)), span=m.span())
        for m in _LOT_RE.finditer(text)
    ]


# ---------------------------------------------------------------------------
# Units of measurement
# ---------------------------------------------------------------------------

_UNIT_WORDS = ("τεμ", "τεμάχια", "τεμάχιο", "kg", "κιλά", "κιλό", "m2", "m3", "m", "lt", "λίτρα", "λίτρο")
_UNIT_RE = re.compile(
    rf"\b(\d+(?:[.,]\d+)?)\s*({'|'.join(re.escape(u) for u in sorted(_UNIT_WORDS, key=len, reverse=True))})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedUnitQuantity:
    raw_value: str
    quantity: str  # kept as raw text — decimal separator ambiguity isn't worth resolving for a unit count
    unit: str
    span: tuple[int, int]


def extract_unit_quantities(text: str) -> list[ExtractedUnitQuantity]:
    return [
        ExtractedUnitQuantity(raw_value=m.group(0), quantity=m.group(1), unit=m.group(2), span=m.span())
        for m in _UNIT_RE.finditer(text)
    ]


# ---------------------------------------------------------------------------
# IBAN (opt-in — §23.3 "μόνο όπου επιτρέπεται": sensitive, callers must set
# DocumentPipelineConfig.extract_iban=True explicitly, never on by default)
# ---------------------------------------------------------------------------

_IBAN_RE = re.compile(r"\bGR\d{2}[ ]?(?:\d{4}[ ]?){5}\d{3}\b")
_IBAN_ALPHABET = {c: str(10 + i) for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}


@dataclass(frozen=True)
class ExtractedIban:
    raw_value: str
    normalized_value: str
    checksum_valid: bool
    span: tuple[int, int]


def _iban_checksum_valid(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    digits = "".join(_IBAN_ALPHABET.get(ch, ch) for ch in rearranged)
    return int(digits) % 97 == 1


def extract_iban(text: str) -> list[ExtractedIban]:
    results = []
    for m in _IBAN_RE.finditer(text):
        normalized = re.sub(r"\s", "", m.group(0)).upper()
        results.append(
            ExtractedIban(
                raw_value=m.group(0),
                normalized_value=normalized,
                checksum_valid=_iban_checksum_valid(normalized),
                span=m.span(),
            )
        )
    return results

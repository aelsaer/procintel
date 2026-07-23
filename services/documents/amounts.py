"""Greek monetary amount extraction from free document text (§23.4).

Must understand all four formats the spec names explicitly:
`1.234,56 €`, `1 234,56 ευρώ`, `€ 1.234,56`, `1,234.56 EUR` — European
(period thousands / comma decimal), space-grouped European, symbol-first,
and US-style (comma thousands / period decimal) respectively.

Design choice: an amount is only extracted when a currency marker (€,
"ευρώ", or "EUR") appears immediately adjacent to a number — a bare number
with no currency context is not "an amount", it's just a number (could be
a CPV code, a year, a protocol number, ...). This is the same
never-guess-on-weak-signal discipline used throughout this codebase
(§7.2's "ποτέ fuzzy match" for identifiers) applied to amounts: a false
amount extraction is worse than a missed one, since §23.5 forbids treating
extracted values as authoritative without review.

Every result keeps `raw_value` (the exact substring as it appeared),
`normalized_amount` (Decimal), `currency`, `vat_inclusion_status`, and
`parser_confidence` — nothing here is ever the sole authoritative source
per §23.5; downstream callers write these to `field_provenance` with
`extraction_method='REGEX'` and let a human/§30.4 evidence drawer confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

VatInclusionStatus = str  # WITH_VAT | WITHOUT_VAT | UNKNOWN

# A monetary number: either thousands-grouped (period/comma/space separator,
# exactly 3 digits per group) with an optional 1-2 digit decimal tail, or a
# plain run of digits with an optional 1-2 digit decimal tail. The 1-2 digit
# constraint on the decimal tail is what disambiguates a decimal separator
# from a thousands separator without any locale assumption — money almost
# never carries a 3-digit fractional part.
_NUMBER = r"\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"

_SYMBOL_BEFORE = re.compile(rf"€\s*(?P<number>{_NUMBER})")
_SYMBOL_AFTER = re.compile(rf"(?P<number>{_NUMBER})\s*€")
_WORD_AFTER = re.compile(rf"(?P<number>{_NUMBER})\s*(?:ευρώ|ΕΥΡΩ|EUR|eur)\b")
_WORD_BEFORE = re.compile(rf"(?:ευρώ|ΕΥΡΩ|EUR|eur)\s*(?P<number>{_NUMBER})")

_GROUPED = re.compile(r"^(\d{1,3}(?:[.,\s]\d{3})+)(?:([.,])(\d{1,2}))?$")
_PLAIN = re.compile(r"^(\d+)(?:([.,])(\d{1,2}))?$")

_WITH_VAT_MARKERS = ("με φπα", "συμπεριλαμβανομένου φπα", "συμπ. φπα")
_WITHOUT_VAT_MARKERS = ("χωρίς φπα", "προ φπα", "πλέον φπα", "άνευ φπα")
_VAT_CONTEXT_WINDOW = 40


@dataclass(frozen=True)
class ExtractedAmount:
    raw_value: str
    normalized_amount: Decimal
    currency: str
    vat_inclusion_status: VatInclusionStatus
    parser_confidence: float
    span: tuple[int, int]


def _normalize_number(number_text: str) -> Decimal | None:
    stripped = "".join(number_text.split())  # collapse internal grouping whitespace, e.g. "1 234"
    match = _GROUPED.match(stripped) or _PLAIN.match(stripped)
    if match is None:
        return None
    int_part, decimal_sep, decimal_digits = match.groups()
    digits_only = re.sub(r"\D", "", int_part)
    if decimal_sep and decimal_digits:
        return Decimal(f"{digits_only}.{decimal_digits}")
    return Decimal(digits_only)


def _vat_status(text: str, start: int, end: int) -> VatInclusionStatus:
    """Nearest-marker attribution, not "any marker within a fixed window" —
    two amounts close together (e.g. a net figure immediately followed by a
    gross figure) each need their *own* nearby VAT marker attributed
    correctly rather than picking up a neighboring amount's marker."""
    best_status: VatInclusionStatus = "UNKNOWN"
    best_distance: int | None = None
    for status, markers in (("WITH_VAT", _WITH_VAT_MARKERS), ("WITHOUT_VAT", _WITHOUT_VAT_MARKERS)):
        for marker in markers:
            for m in re.finditer(re.escape(marker), text, re.IGNORECASE):
                marker_start, marker_end = m.span()
                if marker_end <= start:
                    distance = start - marker_end
                elif marker_start >= end:
                    distance = marker_start - end
                else:
                    distance = 0
                if distance <= _VAT_CONTEXT_WINDOW and (best_distance is None or distance < best_distance):
                    best_distance = distance
                    best_status = status
    return best_status


def extract_amounts(text: str) -> list[ExtractedAmount]:
    results: list[ExtractedAmount] = []
    seen_spans: set[tuple[int, int]] = set()

    for pattern in (_SYMBOL_BEFORE, _SYMBOL_AFTER, _WORD_AFTER, _WORD_BEFORE):
        for m in pattern.finditer(text):
            span = m.span()
            if span in seen_spans:
                continue
            normalized = _normalize_number(m.group("number"))
            if normalized is None:
                continue
            seen_spans.add(span)
            results.append(
                ExtractedAmount(
                    raw_value=m.group(0),
                    normalized_amount=normalized,
                    currency="EUR",
                    vat_inclusion_status=_vat_status(text, *span),
                    parser_confidence=0.95,
                    span=span,
                )
            )

    results.sort(key=lambda r: r.span[0])
    return results

"""Deterministic compliance-field extraction and document term comparison."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Iterable

PARSER_VERSION = "document-intelligence-v1"

_SENTENCE_SPLIT = re.compile(r"(?<=[.;:!?])\s+|\n+")
_SPACE = re.compile(r"\s+")
_DATE = re.compile(
    r"\b(?P<day>[0-3]?\d)[./-](?P<month>[01]?\d)[./-](?P<year>20\d{2})"
    r"(?:\s+(?:και\s+)?(?:ώρα\s*)?(?P<hour>[0-2]?\d)[:.](?P<minute>[0-5]\d))?\b"
)
_AMOUNT = re.compile(
    r"(?P<amount>\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|ευρώ|EUR)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(?P<percent>\d+(?:[.,]\d+)?)\s*%")
_DURATION = re.compile(
    r"\b(?P<number>\d+)\s*(?P<unit>ημέρ(?:α|ες)|μην(?:ός|ών|ες|α)|έτ(?:ος|η)|"
    r"days?|months?|years?)\b",
    re.IGNORECASE,
)
_CPV = re.compile(r"\b(?P<cpv>\d{8})(?:-\d)?\b")
_REQUIREMENT = re.compile(
    r"\b(απαιτ(?:εί|είται|ούνται)|πρέπει\s+να|υποχρεούται|δικαιολογητικ|"
    r"πιστοποιητικ|εγγυητικ|προθεσμί|τεχνικ(?:ή|ές)\s+απαίτησ|"
    r"shall|required|must|certificate|deadline)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ComplianceField:
    document_id: Any
    page_number: int
    category: str
    field_name: str
    value: dict[str, Any]
    source_excerpt: str
    extraction_method: str
    confidence: Decimal


def _compact(value: str) -> str:
    return _SPACE.sub(" ", value).strip(" \t\r\n-•")


def _sentences(text: str) -> Iterable[str]:
    for sentence in _SENTENCE_SPLIT.split(text):
        compact = _compact(sentence)
        if 12 <= len(compact) <= 1500:
            yield compact


def _decimal(value: str) -> str | None:
    normalized = value.replace(" ", "")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        return str(Decimal(normalized))
    except InvalidOperation:
        return None


def _normalized_date(match: re.Match[str]) -> str | None:
    try:
        parsed = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour") or 0),
            int(match.group("minute") or 0),
        )
    except ValueError:
        return None
    return parsed.isoformat()


def _context_field(sentence: str, *, value_type: str) -> tuple[str, str, Decimal]:
    normalized = sentence.casefold()
    if value_type == "DATE":
        if any(term in normalized for term in ("υποβολ", "προσφορ", "deadline")):
            return "TIMELINE", "submission_deadline", Decimal("0.94")
        if any(term in normalized for term in ("έναρξ", "start")):
            return "TIMELINE", "start_date", Decimal("0.84")
        if any(term in normalized for term in ("λήξ", "expiration", "expiry")):
            return "TIMELINE", "end_date", Decimal("0.84")
        return "TIMELINE", "referenced_date", Decimal("0.70")
    if value_type == "AMOUNT":
        if any(term in normalized for term in ("προϋπολογ", "εκτιμώμεν", "estimated")):
            return "FINANCIAL", "estimated_value", Decimal("0.92")
        if any(term in normalized for term in ("εγγύηση", "εγγυητικ", "guarantee")):
            return "FINANCIAL", "guarantee_amount", Decimal("0.88")
        return "FINANCIAL", "referenced_amount", Decimal("0.72")
    if value_type == "PERCENT":
        if any(term in normalized for term in ("συμμετοχ", "participation")):
            return "FINANCIAL", "participation_guarantee_percent", Decimal("0.91")
        if any(term in normalized for term in ("καλή", "εκτέλεσ", "performance")):
            return "FINANCIAL", "performance_guarantee_percent", Decimal("0.91")
        return "FINANCIAL", "referenced_percent", Decimal("0.70")
    if value_type == "DURATION":
        return "CONTRACT", "duration", Decimal("0.86")
    return "CLASSIFICATION", "cpv_code", Decimal("0.98")


def extract_compliance_fields(pages: Iterable[dict[str, Any]]) -> list[ComplianceField]:
    fields: list[ComplianceField] = []
    seen: set[tuple[Any, int, str, str, str]] = set()
    for page in pages:
        document_id = page["document_id"]
        page_number = int(page["page_number"])
        for sentence in _sentences(str(page.get("text") or "")):
            matches: list[tuple[str, re.Match[str]]] = []
            matches.extend(("DATE", match) for match in _DATE.finditer(sentence))
            matches.extend(("AMOUNT", match) for match in _AMOUNT.finditer(sentence))
            matches.extend(("PERCENT", match) for match in _PERCENT.finditer(sentence))
            matches.extend(("DURATION", match) for match in _DURATION.finditer(sentence))
            matches.extend(("CPV", match) for match in _CPV.finditer(sentence))
            for value_type, match in matches:
                category, field_name, confidence = _context_field(sentence, value_type=value_type)
                raw_value = match.group(0)
                if value_type == "DATE":
                    normalized_value = _normalized_date(match)
                elif value_type == "AMOUNT":
                    normalized_value = _decimal(match.group("amount"))
                elif value_type == "PERCENT":
                    normalized_value = _decimal(match.group("percent"))
                elif value_type == "DURATION":
                    normalized_value = {
                        "quantity": int(match.group("number")),
                        "unit": match.group("unit").casefold(),
                    }
                else:
                    normalized_value = match.group("cpv")
                if normalized_value is None:
                    continue
                value = {
                    "raw": raw_value,
                    "normalized": normalized_value,
                    "value_type": value_type,
                }
                key = (document_id, page_number, category, field_name, repr(normalized_value))
                if key in seen:
                    continue
                seen.add(key)
                fields.append(
                    ComplianceField(
                        document_id=document_id,
                        page_number=page_number,
                        category=category,
                        field_name=field_name,
                        value=value,
                        source_excerpt=sentence[:1200],
                        extraction_method="DETERMINISTIC_PATTERN",
                        confidence=confidence,
                    )
                )
            if _REQUIREMENT.search(sentence):
                normalized = sentence.casefold()
                if any(term in normalized for term in ("πιστοποιη", "certificate")):
                    category, field_name, confidence = "ELIGIBILITY", "certificate_requirement", Decimal("0.84")
                elif any(term in normalized for term in ("τεχνικ", "προδιαγραφ", "technical")):
                    category, field_name, confidence = "TECHNICAL", "technical_requirement", Decimal("0.82")
                elif any(term in normalized for term in ("δικαιολογη", "νομ", "legal")):
                    category, field_name, confidence = "ELIGIBILITY", "eligibility_requirement", Decimal("0.82")
                else:
                    category, field_name, confidence = "COMPLIANCE", "requirement", Decimal("0.72")
                key = (document_id, page_number, category, field_name, sentence.casefold())
                if key not in seen:
                    seen.add(key)
                    fields.append(
                        ComplianceField(
                            document_id=document_id,
                            page_number=page_number,
                            category=category,
                            field_name=field_name,
                            value={"raw": sentence, "normalized": sentence, "value_type": "TEXT"},
                            source_excerpt=sentence[:1200],
                            extraction_method="DETERMINISTIC_REQUIREMENT",
                            confidence=confidence,
                        )
                    )
    return fields


def compare_document_terms(
    base_pages: Iterable[dict[str, Any]],
    comparison_pages: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    base = [
        {"page": int(page["page_number"]), "text": sentence}
        for page in base_pages
        for sentence in _sentences(str(page.get("text") or ""))
    ]
    comparison = [
        {"page": int(page["page_number"]), "text": sentence}
        for page in comparison_pages
        for sentence in _sentences(str(page.get("text") or ""))
    ]
    remaining = set(range(len(comparison)))
    changes: list[dict[str, Any]] = []
    for base_item in base:
        best_index: int | None = None
        best_ratio = 0.0
        normalized_base = base_item["text"].casefold()
        for index in remaining:
            ratio = SequenceMatcher(
                None,
                normalized_base,
                comparison[index]["text"].casefold(),
                autojunk=False,
            ).ratio()
            if ratio > best_ratio:
                best_ratio, best_index = ratio, index
        if best_index is None or best_ratio < 0.52:
            changes.append({"change_type": "REMOVED", "base": base_item, "comparison": None, "similarity": 0})
            continue
        remaining.remove(best_index)
        compared = comparison[best_index]
        if best_ratio < 0.985:
            changes.append(
                {
                    "change_type": "CHANGED",
                    "base": base_item,
                    "comparison": compared,
                    "similarity": round(best_ratio, 4),
                }
            )
    for index in sorted(remaining):
        changes.append(
            {
                "change_type": "ADDED",
                "base": None,
                "comparison": comparison[index],
                "similarity": 0,
            }
        )
    counts = {
        change_type: sum(1 for change in changes if change["change_type"] == change_type)
        for change_type in ("ADDED", "REMOVED", "CHANGED")
    }
    return {
        "summary": (
            f"{counts['ADDED']} added, {counts['REMOVED']} removed and "
            f"{counts['CHANGED']} changed terms."
        ),
        "counts": counts,
        "changes": changes,
    }

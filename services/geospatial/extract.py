"""Deterministic Greek place-of-performance extraction.

Structured execution-place fields win over contracting-authority location.
Text extraction is evidence-bearing and conservative: explicit administrative
phrases and names from the loaded boundary gazetteer are accepted, while a
bare city-looking token in arbitrary prose is not.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable, Iterator, Sequence

_POSTAL_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")
_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^0-9A-ZΑ-Ω]+")
_NUTS_RE = re.compile(r"^(?:EL|GR)[0-9A-Z]{0,4}$", re.IGNORECASE)
_NATIONWIDE = {
    "ΕΛΛΑΔΑ",
    "ΕΛΛΗΝΙΚΗ ΕΠΙΚΡΑΤΕΙΑ",
    "ΟΛΗ Η ΕΛΛΑΔΑ",
    "ΣΕ ΟΛΗ ΤΗΝ ΕΛΛΗΝΙΚΗ ΕΠΙΚΡΑΤΕΙΑ",
    "GREECE",
    "GRC",
    "GR",
    "EL",
}
_ADMIN_PATTERNS = (
    (
        "MUNICIPALITY",
        re.compile(
            r"\b(?:ΔΗΜ(?:ΟΣ|ΟΥ|Ο)|ΔΗΜΟΤΙΚ(?:Η|ΗΣ)\s+ΕΝΟΤΗΤ(?:Α|ΑΣ)|Δ\.?\s*Ε\.?)\s+"
            r"([A-ZΑ-ΩΆΈΉΊΌΎΏΪΫ][A-ZΑ-ΩΆΈΉΊΌΎΏΪΫa-zα-ωάέήίόύώϊϋΐΰ\- ]{2,55})"
        ),
    ),
    (
        "REGIONAL_UNIT",
        re.compile(
            r"\b(?:ΠΕΡΙΦΕΡΕΙΑΚ(?:Η|ΗΣ)\s+ΕΝΟΤΗΤ(?:Α|ΑΣ)|Π\.?\s*Ε\.?|ΝΟΜ(?:ΟΣ|ΟΥ|Ο))\s+"
            r"([A-ZΑ-ΩΆΈΉΊΌΎΏΪΫ][A-ZΑ-ΩΆΈΉΊΌΎΏΪΫa-zα-ωάέήίόύώϊϋΐΰ\- ]{2,55})"
        ),
    ),
)
_STOP_PHRASES = re.compile(
    r"\s+(?:ΓΙΑ|ΣΤΟ|ΣΤΗ|ΣΤΟΝ|ΣΤΗΝ|ΜΕ|ΚΑΙ|ΠΟΥ|ΠΡΟΣ|ΑΠΟ|ΣΤΟ\s+ΠΛΑΙΣΙΟ|ΤΗΣ\s+ΠΡΟΜΗΘΕΙΑΣ)\b.*$",
    re.IGNORECASE,
)


def normalize_place(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    no_marks = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    normalized = no_marks.upper().replace("Σ", "Σ")
    return _SPACE_RE.sub(" ", _NON_WORD_RE.sub(" ", normalized)).strip()


def _clean_place(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value") or value.get("name") or value.get("label") or value.get("key")
    if isinstance(value, list):
        return None
    cleaned = _SPACE_RE.sub(" ", str(value).strip(" \t\r\n,;:."))
    if len(cleaned) < 2 or normalize_place(cleaned) in _NATIONWIDE or _NUTS_RE.match(cleaned):
        return None
    return cleaned


def _scalar(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("key") or value.get("value")
    if value is None or isinstance(value, (dict, list)):
        return None
    return str(value).strip() or None


def _flatten_codes(value: Any) -> Iterator[str]:
    if isinstance(value, list):
        for item in value:
            yield from _flatten_codes(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"key", "code"}:
                scalar = _scalar(item)
                if scalar and _NUTS_RE.match(scalar):
                    yield scalar.upper()
            else:
                yield from _flatten_codes(item)
    else:
        scalar = _scalar(value)
        if scalar and _NUTS_RE.match(scalar):
            yield scalar.upper()


@dataclass(frozen=True)
class AdminUnit:
    boundary_type: str
    code: str | None
    name: str
    nuts_code: str | None
    latitude: float
    longitude: float


@dataclass(frozen=True)
class LocationCandidate:
    place_text: str
    postal_code: str | None
    nuts_codes: tuple[str, ...]
    granularity_hint: str
    confidence: float
    source_paths: tuple[str, ...]
    extraction_method: str


def _candidate(
    value: Any,
    *,
    path: str,
    confidence: float,
    nuts_codes: tuple[str, ...],
    postal_code: str | None = None,
    granularity: str = "LOCALITY",
    method: str = "STRUCTURED_FIELD",
) -> LocationCandidate | None:
    place = _clean_place(value)
    if not place:
        return None
    return LocationCandidate(
        place_text=place,
        postal_code=postal_code,
        nuts_codes=nuts_codes,
        granularity_hint=granularity,
        confidence=confidence,
        source_paths=(path,),
        extraction_method=method,
    )


def _text_sources(raw: dict[str, Any], document_texts: Sequence[str]) -> Iterator[tuple[str, str]]:
    for key in ("title", "description", "shortDescription", "subject"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            yield f"$.{key}", value
    for index, detail in enumerate(raw.get("objectDetailsList") or []):
        if not isinstance(detail, dict):
            continue
        description = detail.get("shortDescription") or detail.get("description")
        if isinstance(description, str) and description.strip():
            yield f"$.objectDetailsList[{index}].shortDescription", description
    for index, text in enumerate(document_texts):
        if text.strip():
            yield f"document_pages[{index}]", text


def _aliases(unit: AdminUnit) -> set[str]:
    normalized = normalize_place(unit.name)
    aliases = {normalized}
    for prefix in ("ΔΗΜΟΣ ", "ΠΕΡΙΦΕΡΕΙΑΚΗ ΕΝΟΤΗΤΑ ", "ΠΕΡΙΦΕΡΕΙΑ ", "ΝΟΜΟΣ "):
        if normalized.startswith(prefix):
            aliases.add(normalized[len(prefix) :])
    return {alias for alias in aliases if len(alias) >= 5}


def _text_candidates(
    text: str,
    *,
    path: str,
    units: Sequence[AdminUnit],
    nuts_codes: tuple[str, ...],
) -> Iterator[LocationCandidate]:
    postal_match = _POSTAL_RE.search(text)
    postal_code = postal_match.group(1) if postal_match else None
    for granularity, pattern in _ADMIN_PATTERNS:
        for match in pattern.finditer(text):
            place = _STOP_PHRASES.sub("", match.group(1)).strip(" ,.;:()[]")
            candidate = _candidate(
                place,
                path=f"{path}:explicit-{granularity.lower()}",
                confidence=0.84,
                nuts_codes=nuts_codes,
                postal_code=postal_code,
                granularity=granularity,
                method="TEXT_PATTERN",
            )
            if candidate:
                yield candidate

    normalized_text = f" {normalize_place(text)} "
    matches = 0
    for unit in units:
        if matches >= 20:
            break
        if unit.nuts_code and nuts_codes and not any(
            unit.nuts_code.startswith(code) or code.startswith(unit.nuts_code) for code in nuts_codes
        ):
            continue
        if any(f" {alias} " in normalized_text for alias in _aliases(unit)):
            yield LocationCandidate(
                place_text=unit.name,
                postal_code=postal_code,
                nuts_codes=tuple(dict.fromkeys((*nuts_codes, *((unit.nuts_code,) if unit.nuts_code else ())))),
                granularity_hint=unit.boundary_type,
                confidence=0.76 if path.startswith("document_pages") else 0.81,
                source_paths=(f"{path}:gazetteer",),
                extraction_method="GAZETTEER_TEXT_MATCH",
            )
            matches += 1


def _dedupe(candidates: Iterable[LocationCandidate]) -> list[LocationCandidate]:
    merged: dict[str, LocationCandidate] = {}
    for candidate in candidates:
        key = normalize_place(candidate.place_text)
        previous = merged.get(key)
        if previous is None:
            merged[key] = candidate
            continue
        strongest = previous if previous.confidence >= candidate.confidence else candidate
        merged[key] = replace(
            strongest,
            postal_code=strongest.postal_code or previous.postal_code or candidate.postal_code,
            confidence=max(previous.confidence, candidate.confidence),
            nuts_codes=tuple(dict.fromkeys((*previous.nuts_codes, *candidate.nuts_codes))),
            source_paths=tuple(dict.fromkeys((*previous.source_paths, *candidate.source_paths))),
        )
    return sorted(merged.values(), key=lambda item: (-item.confidence, normalize_place(item.place_text)))


def extract_location_candidates(
    raw: dict[str, Any],
    *,
    document_texts: Sequence[str] = (),
    admin_units: Sequence[AdminUnit] = (),
) -> list[LocationCandidate]:
    nuts_codes = tuple(
        dict.fromkeys(
            (
                *_flatten_codes(raw.get("nutsCode")),
                *_flatten_codes(raw.get("nutsCodes")),
                *_flatten_codes(raw.get("place-of-performance")),
                *_flatten_codes(raw.get("placeOfPerformance")),
            )
        )
    )
    top_postal = _scalar(raw.get("nutsPostalCode") or raw.get("postalCode"))
    candidates: list[LocationCandidate] = []

    details = raw.get("objectDetailsList") or raw.get("lots") or []
    if isinstance(details, list):
        for index, detail in enumerate(details):
            if not isinstance(detail, dict):
                continue
            for key in ("city", "placeOfPerformance", "deliveryPlace", "location"):
                candidate = _candidate(
                    detail.get(key),
                    path=f"$.objectDetailsList[{index}].{key}",
                    confidence=0.97,
                    nuts_codes=nuts_codes,
                    postal_code=_scalar(detail.get("postalCode")),
                )
                if candidate:
                    candidates.append(candidate)

    for key, confidence in (
        ("nutsCity", 0.88),
        ("placeOfPerformanceCity", 0.96),
        ("performanceCity", 0.96),
        ("deliveryCity", 0.94),
        ("city", 0.82),
    ):
        candidate = _candidate(
            raw.get(key),
            path=f"$.{key}",
            confidence=confidence,
            nuts_codes=nuts_codes,
            postal_code=top_postal,
        )
        if candidate:
            candidates.append(candidate)

    for path, text in _text_sources(raw, document_texts):
        candidates.extend(_text_candidates(text, path=path, units=admin_units, nuts_codes=nuts_codes))

    return _dedupe(candidates)

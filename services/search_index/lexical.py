"""Shared lexical normalization for procurement title search."""

from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[a-z0-9α-ω]+")


def normalize_lexical_text(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value.casefold())
    accentless = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )
    return " ".join(_TOKEN_RE.findall(accentless.replace("ς", "σ")))


def stem_lexical_token(token: str) -> str:
    suffixes = (
        "οποιησεων", "οποιηση", "ησεων", "ησησ", "ηση", "ησεισ", "ησης", "ησεις",
        "ικουσ", "ικους", "ικων", "ικεσ", "ικες", "ικησ", "ικης", "ικοι",
        "ικη", "ικο", "σεων", "σησ", "σεισ", "σης", "σεις", "ουσ", "ους", "ων",
        "εσ", "ες", "οσ", "ος", "ησ", "ης", "ιου", "ου", "ια", "ιο", "ασ",
        "ας", "α", "η", "ο",
    )
    for suffix in suffixes:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def query_token_patterns(value: str, *, limit: int = 12) -> list[str]:
    """Return PostgreSQL POSIX patterns; every returned pattern must match."""
    normalized = normalize_lexical_text(value)
    tokens = list(dict.fromkeys(normalized.split()))[:limit]
    patterns: list[str] = []
    for token in tokens:
        if token == "gis":
            # GIS products commonly appear as ArcGIS, QGIS or WebGIS. A
            # whole-token alternative deliberately excludes LOGISTICS.
            patterns.append(r"(^| )((arc|q|web)?gis)( |$)")
            continue
        escaped = re.escape(stem_lexical_token(token))
        if len(token) <= 3:
            patterns.append(rf"(^| ){escaped}( |$)")
        else:
            patterns.append(rf"(^| ){escaped}[a-z0-9α-ω]*( |$)")
    return patterns


def query_concept_pattern(value: str, *, limit: int = 12) -> str | None:
    """Return one regex that requires every term in a business concept.

    Arrays of these patterns can be matched with PostgreSQL ``~* ANY``:
    concepts are alternatives, while every word within one concept remains
    mandatory.
    """
    patterns = query_token_patterns(value, limit=limit)
    if not patterns:
        return None
    return "".join(f"(?=.*{pattern})" for pattern in patterns) + ".*"


def query_prefilter(value: str) -> str:
    """Selective trigram prefilter; strict patterns remain authoritative."""
    tokens = normalize_lexical_text(value).split()
    if not tokens:
        return ""
    stems = [stem_lexical_token(token) for token in tokens]
    return f"%{max(stems, key=len)}%"


def lexical_query_matches(query: str, text: str | None) -> bool:
    normalized_text = normalize_lexical_text(text)
    patterns = query_token_patterns(query)
    return bool(patterns) and all(
        re.search(pattern, normalized_text, flags=re.IGNORECASE) is not None
        for pattern in patterns
    )


# SQL counterpart of normalize_lexical_text(). It is intentionally an
# expression rather than an extra required column so upgraded APIs remain
# compatible while the optional GIN index migration is being applied.
def normalized_text_sql(column: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.]*", column):
        raise ValueError("column must be a static SQL identifier")
    return f"""
        regexp_replace(
            translate(
                lower(COALESCE({column}, '')),
                'άέήίόύώϊΐϋΰς',
                'αεηιουωιιυυσ'
            ),
            '[^a-z0-9α-ω]+',
            ' ',
            'g'
        )
    """

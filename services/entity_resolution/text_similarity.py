"""Lightweight title/name similarity shared by every fuzzy-matching tier in
this codebase (Διαύγεια §17.4 `DIAVGEIA_SEARCH_MATCH`, ΑΝΑΠΤΥΞΗ §19.2 Level
4, TED §21.3 Level 4). Deliberately not a real fuzzy-matching library
(rapidfuzz/thefuzz) — stdlib `difflib.SequenceMatcher` is good enough to
gate a confidence threshold on a handful of already-narrowed-down
candidates, which is all any of these tiers need it for; it is not used to
rank or search a large corpus.
"""

from __future__ import annotations

from difflib import SequenceMatcher


def normalized_similarity(a: str | None, b: str | None) -> float:
    """Case/whitespace-insensitive similarity ratio in [0.0, 1.0]. Either
    side missing is defined as no similarity (0.0), not an error — callers
    matching against optional fields (e.g. an organization label that
    wasn't captured) should treat that as "no evidence", not crash."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().upper(), b.strip().upper()).ratio()

"""Pure onboarding rules.

Database persistence stays in the API router; these rules are kept pure so
profile quality and first-session ranking remain deterministic and testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def normalize_cpv_codes(codes: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw in codes:
        code = "".join(
            character for character in str(raw).split("-", 1)[0] if character.isdigit()
        )
        if 2 <= len(code) <= 8 and code not in normalized:
            normalized.append(code)
    return normalized


def normalize_terms(terms: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw in terms:
        term = " ".join(str(raw).strip().split())
        if len(term) >= 2 and term.casefold() not in {
            existing.casefold() for existing in normalized
        }:
            normalized.append(term)
    return normalized


def profile_quality(
    *,
    description: str,
    cpv_codes: Iterable[str],
    keywords: Iterable[str],
    opportunity_count: int,
) -> tuple[float, list[dict[str, Any]]]:
    cpvs = normalize_cpv_codes(cpv_codes)
    terms = normalize_terms(keywords)
    findings: list[dict[str, Any]] = []
    score = 0.0

    description_words = len(description.split())
    if description_words >= 12:
        score += 30
    else:
        findings.append(
            {
                "code": "DESCRIPTION_TOO_SHORT",
                "severity": "WARNING",
                "message": "Προσθέστε προϊόντα, υπηρεσίες και τύπους έργων που αναλαμβάνετε.",
            }
        )

    if cpvs:
        score += min(35, 20 + len(cpvs) * 3)
    else:
        findings.append(
            {
                "code": "NO_CPV_CONFIRMED",
                "severity": "ERROR",
                "message": "Επιβεβαιώστε τουλάχιστον έναν κωδικό CPV.",
            }
        )

    if terms:
        score += min(15, 5 + len(terms) * 2)
    else:
        findings.append(
            {
                "code": "NO_KEYWORDS",
                "severity": "INFO",
                "message": "Προσθέστε ειδικούς όρους που ξεχωρίζουν το αντικείμενό σας.",
            }
        )

    score += min(20, max(0, opportunity_count) * 2)
    if opportunity_count < 3:
        findings.append(
            {
                "code": "LOW_INITIAL_COVERAGE",
                "severity": "WARNING",
                "message": "Βρέθηκαν λίγες αυστηρές αντιστοιχίσεις. Συνιστάται ανθρώπινος έλεγχος.",
            }
        )

    return round(min(100.0, score), 2), findings


def rank_initial_opportunities(
    opportunities: Iterable[Mapping[str, Any]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    def key(item: Mapping[str, Any]) -> tuple[float, float, float]:
        score = float(item.get("score") or 0)
        confidence = float(item.get("data_confidence") or 0)
        recency = float(item.get("recency_rank") or 0)
        return score, confidence, recency

    deduplicated: dict[str, Mapping[str, Any]] = {}
    for opportunity in opportunities:
        process_id = str(opportunity.get("process_id") or "")
        if not process_id:
            continue
        current = deduplicated.get(process_id)
        if current is None or key(opportunity) > key(current):
            deduplicated[process_id] = opportunity
    return [
        dict(item)
        for item in sorted(deduplicated.values(), key=key, reverse=True)[:limit]
    ]

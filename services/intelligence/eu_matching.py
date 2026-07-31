"""Pure scoring and scheduling helpers for European TED intelligence."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

EU_MEMBER_COUNTRIES = (
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FR",
    "GR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
)

COUNTRY_NAMES_EL = {
    "AT": "Αυστρία",
    "BE": "Βέλγιο",
    "BG": "Βουλγαρία",
    "HR": "Κροατία",
    "CY": "Κύπρος",
    "CZ": "Τσεχία",
    "DE": "Γερμανία",
    "DK": "Δανία",
    "EE": "Εσθονία",
    "ES": "Ισπανία",
    "FI": "Φινλανδία",
    "FR": "Γαλλία",
    "GR": "Ελλάδα",
    "HU": "Ουγγαρία",
    "IE": "Ιρλανδία",
    "IT": "Ιταλία",
    "LT": "Λιθουανία",
    "LU": "Λουξεμβούργο",
    "LV": "Λετονία",
    "MT": "Μάλτα",
    "NL": "Ολλανδία",
    "PL": "Πολωνία",
    "PT": "Πορτογαλία",
    "RO": "Ρουμανία",
    "SE": "Σουηδία",
    "SI": "Σλοβενία",
    "SK": "Σλοβακία",
}


def _normalized_text(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return " ".join(
        re.sub(r"[^a-zA-Z0-9\u0370-\u03ff]+", " ", decomposed)
        .casefold()
        .split()
    )


def _keyword_matches(keyword: str, title: str | None) -> bool:
    terms = [term for term in _normalized_text(keyword).split() if len(term) >= 2]
    haystack = _normalized_text(title)
    return bool(terms) and all(term in haystack for term in terms)


def countries_for_day(
    day: date,
    *,
    countries: Iterable[str] = EU_MEMBER_COUNTRIES,
    batch_size: int = 5,
    always: Iterable[str] = ("GR",),
) -> tuple[str, ...]:
    configured = tuple(dict.fromkeys(code.strip().upper() for code in countries if code.strip()))
    pinned = tuple(code for code in dict.fromkeys(code.upper() for code in always) if code in configured)
    rotating = tuple(code for code in configured if code not in pinned)
    if not rotating or batch_size <= len(pinned):
        return pinned[:batch_size]
    take = min(batch_size - len(pinned), len(rotating))
    start = day.toordinal() % len(rotating)
    selected = tuple(rotating[(start + offset) % len(rotating)] for offset in range(take))
    return pinned + selected


def date_windows(date_from: date, date_to: date, window_days: int) -> list[tuple[date, date]]:
    if date_to < date_from:
        raise ValueError("date_to must not be before date_from")
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    windows = []
    cursor = date_from
    while cursor <= date_to:
        end = min(cursor + timedelta(days=window_days - 1), date_to)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    return windows


def official_ted_url(publication_number: str | None, notice_id: str) -> str:
    identifier = (publication_number or notice_id).strip()
    return f"https://ted.europa.eu/en/notice/-/detail/{identifier}"


def cross_border_match(
    *,
    title: str | None,
    cpv_codes: Iterable[str],
    profile_cpv_prefixes: Iterable[str],
    profile_keywords: Iterable[str],
    amount: Decimal | None,
    amount_min: Decimal | None,
    amount_max: Decimal | None,
    deadline: datetime | None,
    country_code: str,
    parse_confidence: float,
    as_of: date,
) -> tuple[Decimal, list[str], list[str], bool]:
    cpvs = tuple(str(code).strip() for code in cpv_codes if str(code).strip())
    prefixes = tuple(str(code).strip() for code in profile_cpv_prefixes if str(code).strip())
    keywords = tuple(value.strip() for value in profile_keywords if value.strip())
    matched_cpvs = sorted(
        {
            code
            for code in cpvs
            for prefix in prefixes
            if code.startswith(prefix) or prefix.startswith(code)
        }
    )
    matched_keywords = sorted(value for value in keywords if _keyword_matches(value, title))
    exact_cpv = any(len(prefix) >= 8 and code.startswith(prefix) for code in cpvs for prefix in prefixes)

    reasons: list[str] = []
    barriers: list[str] = []
    score = Decimal("0")
    if matched_cpvs:
        score += Decimal("50")
        reasons.append(f"CPV fit: {', '.join(matched_cpvs[:3])}")
    if matched_keywords:
        score += Decimal("20")
        reasons.append(f"Title fit: {', '.join(matched_keywords[:3])}")
    elif keywords and not exact_cpv:
        barriers.append("The configured business terms are absent from the notice title.")

    now = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    if deadline is not None:
        normalized_deadline = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
        days_remaining = (normalized_deadline - now).days
        if days_remaining < 0:
            barriers.append("The recorded submission deadline has passed.")
        elif days_remaining <= 7:
            score += Decimal("5")
            reasons.append(f"Open for {days_remaining} days; urgent review.")
        else:
            score += Decimal("15")
            reasons.append(f"Open for {days_remaining} days.")
    else:
        score += Decimal("5")
        barriers.append("TED did not return a submission deadline; verify the official notice.")

    if amount is None:
        score += Decimal("4")
        barriers.append("No comparable notice value was published.")
    elif amount_min is not None and amount < amount_min:
        barriers.append("Value is below the configured minimum.")
    elif amount_max is not None and amount > amount_max:
        barriers.append("Value is above the configured maximum.")
    else:
        score += Decimal("10")
        reasons.append("Value is within the configured commercial range.")

    confidence_points = Decimal(str(max(0.0, min(parse_confidence, 1.0)))) * Decimal("5")
    score += confidence_points
    if parse_confidence < 0.75:
        barriers.append("TED parsing confidence is low; inspect the official notice.")
    if country_code.upper() != "GR":
        barriers.append("Cross-border legal, language and registration requirements need review.")

    eligible = bool(matched_cpvs or matched_keywords) and not any(
        barrier.startswith("The recorded submission deadline has passed")
        for barrier in barriers
    )
    if keywords and not matched_keywords and not exact_cpv:
        eligible = False
    return min(score.quantize(Decimal("0.01")), Decimal("100.00")), reasons, barriers, eligible

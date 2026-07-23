"""Explainable business-description classification against the full CPV tree."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from collections.abc import Iterable


@dataclass(frozen=True)
class ClassifiedTerm:
    term_type: str
    value: str
    label: str
    confidence: float
    reason: str
    source: str = "RULE"


@dataclass(frozen=True)
class CpvCatalogEntry:
    code: str
    description_el: str | None
    description_en: str | None
    parent_code: str | None = None


_CPV_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("72", "Υπηρεσίες πληροφορικής", ("λογισμικ", "software", "cloud", "data platform", "πληροφορικ", "κυβερνοασφαλ", "ψηφιακ")),
    ("48", "Πακέτα λογισμικού", ("λογισμικ", "software", "erp", "crm", "platform")),
    ("30", "Εξοπλισμός γραφείου και Η/Υ", ("υπολογιστ", "laptop", "server", "εκτυπωτ", "hardware")),
    ("79", "Επιχειρηματικές και συμβουλευτικές υπηρεσίες", ("συμβουλευ", "μελετ", "consulting", "επικοινων", "marketing")),
    ("71", "Αρχιτεκτονικές και τεχνικές υπηρεσίες", ("μηχανικ", "αρχιτεκτον", "τεχνικ μελετ", "επιβλεψ")),
    ("45", "Κατασκευαστικές εργασίες", ("κατασκευ", "οικοδομ", "οδοποι", "ανακαιν", "εργολαβ")),
    ("90", "Περιβάλλον, καθαριότητα και απόβλητα", ("υπηρεσι καθαρισ", "καθαριοτ", "αποβλητ", "ανακυκλ", "περιβαλλον", "απορριμμ")),
    ("85", "Υγεία και κοινωνική μέριμνα", ("υγει", "ιατρ", "νοσηλ", "κοινωνικ φροντιδ")),
    ("33", "Ιατρικός εξοπλισμός", ("ιατρικ εξοπλισ", "διαγνωσ", "φαρμακευτ", "νοσοκομειακ")),
    ("80", "Εκπαίδευση και κατάρτιση", ("εκπαιδευ", "καταρτισ", "σεμιναρ", "e-learning")),
    ("50", "Επισκευή και συντήρηση", ("συντηρησ", "επισκευ", "τεχνικη υποστηριξ")),
    ("60", "Μεταφορές", ("μεταφορ", "logistics", "διανομη", "ταχυδρομ")),
    ("55", "Ξενοδοχειακές και επισιτιστικές υπηρεσίες", ("catering", "επισιτισ", "ξενοδοχ", "φιλοξεν")),
    ("09", "Καύσιμα και ενέργεια", ("ενεργ", "καυσιμ", "ηλεκτρικ", "φυσικο αεριο", "φωτοβολτα")),
    ("34", "Οχήματα και εξοπλισμός μεταφορών", ("οχημα", "αυτοκινητ", "λεωφορει", "φορτηγ")),
)

# Colloquial business language often differs from the legal CPV label. These
# aliases bridge known Greek procurement terminology; the catalogue matcher
# below handles the rest of the 9,000+ official codes.
_CPV_SYNONYMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "38221000",
        "Συστήματα γεωγραφικών πληροφοριών (GIS ή ισοδύναμα)",
        ("gis", "arcgis", "qgis", "webgis", "γεωγραφικ συστημα πληροφορι", "γεωπληροφοριακ"),
    ),
    (
        "48326000",
        "Πακέτα λογισμικού χαρτογράφησης",
        ("λογισμικ χαρτογραφ", "mapping software"),
    ),
    (
        "48326100",
        "Σύστημα ψηφιακής χαρτογράφησης",
        ("ψηφιακ χαρτογραφ", "digital mapping"),
    ),
    ("77312000", "Υπηρεσίες εκκαθάρισης από αγριόχορτα", ("αποψιλ", "αγριοχορτ", "καθαρισ οικοπεδ", "καθαρισ βλαστησ")),
    ("77312100", "Υπηρεσίες εξόντωσης ζιζανίων", ("ζιζανιοκτον", "ζιζανιοκτο", "καταπολεμησ ζιζαν")),
    ("77314000", "Υπηρεσίες συντήρησης οικοπέδων", ("κοπη χορτ", "χορτοκοπ", "συντηρησ οικοπεδ")),
    ("45111220", "Εργασίες απομάκρυνσης θαμνώδους βλάστησης", ("θαμνωδ βλαστησ", "απομακρυνσ θαμν", "εκχερσωσ")),
    ("77211400", "Υπηρεσίες κοπής δέντρων", ("κοπη δεντρ", "υλοτομησ δεντρ")),
    ("77341000", "Κλάδεμα δέντρων", ("κλαδεμ", "κλαδευσ")),
)

_STOPWORDS = {
    "και", "για", "των", "την", "τις", "στη", "στο", "στην", "στον", "απο", "που",
    "ειναι", "εχει", "εχουμε", "μας", "μια", "ενα", "ως", "σε", "με", "του", "τους",
    "δημοσιου", "δημοσιες", "ιδιωτικου", "ιδιωτικες", "λυσεις", "εργα", "εργο", "υπηρεσιες",
    "with", "the", "and", "for", "services", "service", "solutions", "company", "business",
    "εταιρεια", "παρεχουμε", "αναπτυσσουμε", "προσφερουμε", "δραστηριοποιουμαστε",
    "κανουμε", "αναλαμβανουμε", "παροχη", "προμηθεια", "εργασιες", "εργασια",
    "εφαρμογες", "συστημα", "συστηματα", "προιοντα", "τεχνολογια",
}


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    accentless = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    return re.sub(r"[^a-z0-9α-ω]+", " ", accentless.replace("ς", "σ")).strip()


_NORMALIZED_STOPWORDS = {normalize_text(word) for word in _STOPWORDS}


def _stem(token: str) -> str:
    """Small language-agnostic procurement stemmer for lexical recall."""
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


def _meaningful_tokens(value: str) -> list[str]:
    return [
        token for token in normalize_text(value).split()
        if len(token) >= 4 and token not in _NORMALIZED_STOPWORDS
    ]


def _token_matches(left: str, right: str) -> bool:
    left_stem, right_stem = _stem(left), _stem(right)
    return (
        left == right
        or left_stem == right_stem
        or (min(len(left_stem), len(right_stem)) >= 6 and (
            left_stem.startswith(right_stem) or right_stem.startswith(left_stem)
        ))
    )


def _keyword_terms(normalized: str, limit: int = 8) -> list[ClassifiedTerm]:
    tokens = [
        token
        for token in normalized.split()
        if token not in _NORMALIZED_STOPWORDS
        and (
            len(token) >= 4
            or (len(token) >= 2 and re.fullmatch(r"[a-z0-9]+", token) is not None)
        )
    ]
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts, key=lambda token: (-counts[token], tokens.index(token)))[:limit]
    return [
        ClassifiedTerm(
            term_type="KEYWORD",
            value=_stem(token),
            label=token,
            confidence=min(0.72 + counts[token] * 0.04, 0.88),
            reason=f"Ο όρος εμφανίζεται {counts[token]} φορά/ές στην περιγραφή της επιχείρησης.",
        )
        for token in ranked
    ]


def _catalog_terms(
    normalized: str,
    catalog: Iterable[CpvCatalogEntry],
    *,
    limit: int = 12,
) -> list[ClassifiedTerm]:
    query_tokens = _meaningful_tokens(normalized)
    if not query_tokens:
        return []

    ranked: list[tuple[float, CpvCatalogEntry, list[str]]] = []
    for entry in catalog:
        label = entry.description_el or entry.description_en or ""
        label_tokens = _meaningful_tokens(label)
        if not label_tokens:
            continue
        matched_query = [
            token for token in query_tokens
            if any(_token_matches(token, label_token) for label_token in label_tokens)
        ]
        if not matched_query:
            continue
        matched_label = [
            token for token in label_tokens
            if any(_token_matches(token, query_token) for query_token in query_tokens)
        ]
        coverage = len(matched_query) / len(query_tokens)
        precision = len(matched_label) / len(label_tokens)
        specificity = min(max(len(entry.code.rstrip("0")) - 2, 0) / 6, 1)
        phrase_bonus = 0.12 if normalize_text(label) in normalized else 0
        score = min(0.52 + 0.22 * coverage + 0.14 * precision + 0.08 * specificity + phrase_bonus, 0.96)
        if coverage >= 0.34 or len(matched_query) >= 2 or phrase_bonus:
            ranked.append((score, entry, matched_query))

    ranked.sort(key=lambda item: (-item[0], -len(item[1].code.rstrip("0")), item[1].code))
    selected: list[ClassifiedTerm] = []
    seen_codes: set[str] = set()
    for score, entry, matches in ranked:
        if entry.code in seen_codes:
            continue
        seen_codes.add(entry.code)
        selected.append(
            ClassifiedTerm(
                term_type="CPV_PREFIX",
                value=entry.code,
                label=entry.description_el or entry.description_en or f"CPV {entry.code}",
                confidence=score,
                reason=f"Αντιστοίχιση με τον επίσημο κατάλογο CPV: {', '.join(matches[:4])}.",
                source="CPV_CATALOG",
            )
        )
        if len(selected) >= limit:
            break
    return selected


def classify_business_description(
    description: str,
    catalog: Iterable[CpvCatalogEntry] = (),
) -> list[ClassifiedTerm]:
    normalized = normalize_text(description)
    if not normalized:
        return []

    terms: list[ClassifiedTerm] = []
    for code, label, needles in _CPV_SYNONYMS:
        matches = [needle for needle in needles if normalize_text(needle) in normalized]
        if not matches:
            continue
        terms.append(
            ClassifiedTerm(
                term_type="CPV_PREFIX",
                value=code,
                label=label,
                confidence=min(0.92 + 0.02 * len(matches), 0.98),
                reason=f"Σημασιολογική αντιστοίχιση όρων: {', '.join(matches[:4])}.",
                source="SYNONYM",
            )
        )
        terms.append(
            ClassifiedTerm(
                term_type="KEYWORD",
                value=_stem(normalize_text(matches[0]).split()[0]),
                label=matches[0],
                confidence=0.91,
                reason="Όρος ανάκλησης για πράξεις όπου η πηγή δεν παρέχει CPV.",
                source="SYNONYM",
            )
        )

    for prefix, label, needles in _CPV_RULES:
        matches = [needle for needle in needles if normalize_text(needle) in normalized]
        if not matches:
            continue
        confidence = min(0.78 + 0.06 * len(matches), 0.96)
        terms.append(
            ClassifiedTerm(
                term_type="CPV_PREFIX",
                value=prefix,
                label=label,
                confidence=confidence,
                reason=f"Αντιστοίχιση όρων: {', '.join(matches[:4])}.",
            )
        )

    terms.extend(_catalog_terms(normalized, catalog))
    terms.extend(_keyword_terms(normalized))
    deduplicated: dict[tuple[str, str], ClassifiedTerm] = {}
    for term in terms:
        key = (term.term_type, term.value)
        current = deduplicated.get(key)
        if current is None or term.confidence > current.confidence:
            deduplicated[key] = term
    return sorted(deduplicated.values(), key=lambda term: (-term.confidence, term.term_type, term.value))

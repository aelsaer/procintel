"""ΓΕΜΗ legal-form / company-status lexicons — closes the "tighten once the
real lexicon is confirmed" TODOs left in `normalize.py` and `cache.py`.

Unlike API hostnames/endpoint paths (never guessed in this codebase), the
Greek legal-form and company-status vocabulary below is standard company-
law terminology, publicly documented independently of ΓΕΜΗ's own API
response shape — ΑΕ/ΕΠΕ/ΙΚΕ/ΟΕ/ΕΕ etc. are established legal entity types,
not something specific to one API's undocumented internals. What's still
unconfirmed is which exact raw string ΓΕΜΗ's API sends for each (hence the
handful of spelling variants mapped per canonical code below) — treat this
as a best-effort normalization, not a source-confirmed contract.

The canonical status codes (`ACTIVE`/`SUSPENDED`/`DISSOLVED`/
`IN_LIQUIDATION`/`DEREGISTERED`/`MERGED`) match the vocabulary hinted at in
`db/migrations/02_identity_and_registry.sql`'s
`entity_company_snapshots.company_status` column comment.

This lexicon is duplicated (deliberately) in `db/seeds/gemi_lexicons.sql`
as queryable reference tables (`gemi_legal_forms`/`gemi_company_statuses`)
for anything that needs to look the vocabulary up directly in SQL (e.g. a
future UI dropdown) — keep both in sync by hand if either changes.
"""

from __future__ import annotations

import unicodedata

LEGAL_FORM_LEXICON: dict[str, str] = {
    "ΑΝΩΝΥΜΗ ΕΤΑΙΡΕΙΑ": "AE",
    "Α.Ε.": "AE",
    "ΑΕ": "AE",
    "ΕΤΑΙΡΕΙΑ ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΕΥΘΥΝΗΣ": "EPE",
    "Ε.Π.Ε.": "EPE",
    "ΕΠΕ": "EPE",
    "ΙΔΙΩΤΙΚΗ ΚΕΦΑΛΑΙΟΥΧΙΚΗ ΕΤΑΙΡΕΙΑ": "IKE",
    "Ι.Κ.Ε.": "IKE",
    "ΙΚΕ": "IKE",
    "ΟΜΟΡΡΥΘΜΗ ΕΤΑΙΡΕΙΑ": "OE",
    "Ο.Ε.": "OE",
    "ΟΕ": "OE",
    "ΕΤΕΡΟΡΡΥΘΜΗ ΕΤΑΙΡΕΙΑ": "EE",
    "Ε.Ε.": "EE",
    "ΕΕ": "EE",
    "ΑΤΟΜΙΚΗ ΕΠΙΧΕΙΡΗΣΗ": "SOLE_PROPRIETORSHIP",
    "ΑΤΟΜΙΚΗ": "SOLE_PROPRIETORSHIP",
    "ΚΟΙΝΩΝΙΑ ΑΣΤΙΚΟΥ ΔΙΚΑΙΟΥ": "CIVIL_LAW_PARTNERSHIP",
    "ΑΣΤΙΚΗ ΕΤΑΙΡΕΙΑ": "CIVIL_LAW_PARTNERSHIP",
    "ΣΥΝΕΤΑΙΡΙΣΜΟΣ": "COOPERATIVE",
    "ΚΟΙΝΟΠΡΑΞΙΑ": "JOINT_VENTURE",
    "ΥΠΟΚΑΤΑΣΤΗΜΑ ΑΛΛΟΔΑΠΗΣ ΕΤΑΙΡΕΙΑΣ": "FOREIGN_BRANCH",
    "ΥΠΟΚΑΤΑΣΤΗΜΑ": "FOREIGN_BRANCH",
}

COMPANY_STATUS_LEXICON: dict[str, str] = {
    "ΕΝΕΡΓΗ": "ACTIVE",
    "ΕΝ ΕΝΕΡΓΕΙΑ": "ACTIVE",
    "ACTIVE": "ACTIVE",
    "ΑΝΕΝΕΡΓΗ": "SUSPENDED",
    "ΣΕ ΑΝΑΣΤΟΛΗ": "SUSPENDED",
    "ΑΝΑΣΤΟΛΗ": "SUSPENDED",
    "ΥΠΟ ΕΚΚΑΘΑΡΙΣΗ": "IN_LIQUIDATION",
    "ΣΕ ΕΚΚΑΘΑΡΙΣΗ": "IN_LIQUIDATION",
    "ΔΙΑΛΥΜΕΝΗ": "DISSOLVED",
    "ΛΥΘΕΙΣΑ": "DISSOLVED",
    "ΔΙΑΓΡΑΜΜΕΝΗ": "DEREGISTERED",
    "ΔΙΑΓΡΑΦΗ": "DEREGISTERED",
    "ΣΥΓΧΩΝΕΥΘΕΙΣΑ": "MERGED",
    "ΣΕ ΣΥΓΧΩΝΕΥΣΗ": "MERGED",
}

# The only status the cache-refresh policy (§18.3) treats as "stable" —
# everything else (including any raw label that fails to normalize) gets
# the shorter "in transition" refresh window, conservatively.
STABLE_STATUSES: frozenset[str] = frozenset({"ACTIVE"})


def _strip_greek_accents(value: str) -> str:
    """Greek legal/official text conventionally drops accents (τόνοι) in
    ALL-CAPS, but raw API text isn't guaranteed to — "Ιδιωτική" and
    "ΙΔΙΩΤΙΚΗ" (no accent) must match the same lexicon entry. NFD
    decomposition splits each accented letter into base+combining-accent,
    then combining marks (Unicode category Mn) are dropped."""
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _lookup(lexicon: dict[str, str], raw_label: str | None) -> str | None:
    if not raw_label:
        return None
    key = _strip_greek_accents(raw_label.strip().upper())
    if key in lexicon:
        return lexicon[key]
    # unrecognized label — best-effort passthrough (normalized casing/
    # accents/whitespace only) rather than silently dropping it; better to
    # store something imperfect than nothing at all, consistent with this
    # codebase's general "never reject, flag instead" discipline.
    return key


def normalize_legal_form_code(raw_label: str | None) -> str | None:
    return _lookup(LEGAL_FORM_LEXICON, raw_label)


def normalize_company_status(raw_label: str | None) -> str | None:
    return _lookup(COMPANY_STATUS_LEXICON, raw_label)

"""Cache/refresh policy — description.txt §18.3:

    νέα εταιρεία: άμεσο lookup
    ενεργή εταιρεία: refresh ανά 30 ημέρες
    εταιρεία σε μεταβολή: συχνότερο refresh
    αρνητικό αποτέλεσμα: επανέλεγχος αργότερα, όχι μόνιμο negative cache

"In transition" status detection now uses `lexicon.STABLE_STATUSES` — the
canonical vocabulary `normalize.py` already normalizes every incoming
`company_status` against before it's ever stored, so this check is a
simple membership test, not a raw-label guess anymore. The lexicon itself
is still a best-effort mapping of *raw ΓΕΜΗ label spellings* (real Greek
company-law terminology, but not confirmed against a live payload) — see
`lexicon.py`'s module docstring.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .lexicon import STABLE_STATUSES

ACTIVE_REFRESH = timedelta(days=30)
TRANSITION_REFRESH = timedelta(days=7)
NEGATIVE_RESULT_REFRESH = timedelta(days=7)  # never permanent, per §18.3


def is_stable_status(company_status: str | None) -> bool:
    if company_status is None:
        return False
    return company_status.strip().upper() in STABLE_STATUSES


def should_refresh(*, last_checked_at: datetime | None, company_status: str | None, now: datetime) -> bool:
    """True if a new ΓΕΜΗ lookup should be made now. `company_status` is the
    *current* snapshot's status if one exists, else None (new company or a
    prior negative result — both get the shortest window)."""
    if last_checked_at is None:
        return True

    age = now - last_checked_at
    if company_status is None:
        return age >= NEGATIVE_RESULT_REFRESH
    if is_stable_status(company_status):
        return age >= ACTIVE_REFRESH
    return age >= TRANSITION_REFRESH

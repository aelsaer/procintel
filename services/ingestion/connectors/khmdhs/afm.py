"""Greek ΑΦΜ (VAT) checksum validation — description.txt §7.2, reproduced
verbatim from the spec's reference implementation.

A failed checksum does NOT mean the source record is rejected. Callers
should set `identifier_valid = false` and `match_eligibility = restricted`
on the resulting `entity_identifiers` row (data_quality_issue
`INVALID_AFM_CHECKSUM`) instead of dropping the record.
"""

from __future__ import annotations


def valid_greek_afm(value: str) -> bool:
    afm = "".join(ch for ch in value if ch.isdigit())

    if len(afm) != 9:
        return False

    total = sum(int(afm[i]) * (2 ** (8 - i)) for i in range(8))
    expected = (total % 11) % 10

    return expected == int(afm[8])

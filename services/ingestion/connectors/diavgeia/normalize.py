"""Διαύγεια decision -> canonical shape (spec §17.2).

Field names below are a best-effort guess — description.txt lists what a
decision carries conceptually (subject, type, date, protocol number,
issuing authority, organizational unit, signers, document URL, version log)
but not the JSON field names themselves, since no sample payload was
available. Fix here once a real payload is confirmed
(docs/source-contracts/diavgeia.md, Στάδιο 0); each field tries a couple of
plausible names as a soft hedge, same pattern as
`services/ingestion/connectors/khmdhs/normalize.py`.

Issuing authority / organizational unit are kept as plain text, not
resolved to a canonical `entities` row: description.txt's matching
hierarchy (§8) never allows a name alone to justify creating or merging an
entity, and no reliable identifier (ΑΦΜ, ΓΕΜΗ, ...) is confirmed present on
a Διαύγεια decision payload.

Signers are the one explicit exception to that rule: §6.3 explicitly
permits storing natural persons as `PERSON` entities, name-only, when
"necessary to represent a publicly published act, such as the signer" —
so `signer_names` here (and `db_writer.py`'s find-or-create-by-normalized-
name for them) is deliberately different from how the issuing authority is
handled, not an inconsistency.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


def _as_name_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v and str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def normalize_ada(value: str) -> str:
    """§7.2: ΑΔΑ — uppercase, strip whitespace, never fuzzy-matched."""
    return value.strip().upper()


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000).date()
        except (OSError, OverflowError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


class NormalizedDecision(BaseModel):
    ada_raw: str
    ada_normalized: str
    subject: str | None = None
    decision_type: str | None = None
    decision_date: date | None = None
    protocol_number: str | None = None
    issuing_authority_name: str | None = None
    organizational_unit_name: str | None = None
    document_url: str | None = None
    signer_names: list[str] = []


def normalize_decision_record(raw: dict[str, Any], *, ada: str) -> NormalizedDecision:
    signers_raw = raw.get("signers") or raw.get("signatories") or raw.get("υπογράφοντες")
    if isinstance(signers_raw, list):
        signer_names = [
            (s.get("name") if isinstance(s, dict) else s) for s in signers_raw
        ]
    else:
        signer_names = signers_raw

    return NormalizedDecision(
        ada_raw=ada,
        ada_normalized=normalize_ada(ada),
        subject=raw.get("subject") or raw.get("θέμα"),
        decision_type=raw.get("type") or raw.get("decisionTypeLabel"),
        decision_date=_to_date(raw.get("issueDate") or raw.get("decisionDate")),
        protocol_number=raw.get("protocolNumber"),
        issuing_authority_name=raw.get("organizationLabel") or raw.get("issuingAuthority"),
        organizational_unit_name=raw.get("unitLabel") or raw.get("organizationalUnit"),
        document_url=raw.get("documentUrl") or raw.get("url"),
        signer_names=_as_name_list(signer_names),
    )

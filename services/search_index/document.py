"""Builds the OpenSearch document for one `procurement_acts` row — a pure
function over already-fetched data, so it's unit-testable without a
database or OpenSearch connection. `indexer.py` is what actually fetches
the related rows (CPV/NUTS/parties) and calls this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ActForIndexing:
    id: str
    process_id: str | None
    adam: str | None
    ada_list: list[str]
    title: str | None
    normalized_title: str | None
    act_type: str
    status: str | None
    procedure_type: str | None
    amount_net: Decimal | None
    amount_gross: Decimal | None
    currency: str | None
    cpv_codes: list[str] = field(default_factory=list)
    nuts_codes: list[str] = field(default_factory=list)
    buyer_id: str | None = None
    buyer_name: str | None = None
    supplier_ids: list[str] = field(default_factory=list)
    supplier_names: list[str] = field(default_factory=list)
    submission_date: date | None = None
    decision_date: date | None = None


def build_act_document(act: ActForIndexing) -> dict:
    return {
        "id": act.id,
        "process_id": act.process_id,
        "adam": act.adam,
        "ada_list": act.ada_list,
        "title": act.title,
        "normalized_title": act.normalized_title,
        "act_type": act.act_type,
        "status": act.status,
        "procedure_type": act.procedure_type,
        "amount_net": float(act.amount_net) if act.amount_net is not None else None,
        "amount_gross": float(act.amount_gross) if act.amount_gross is not None else None,
        "currency": act.currency,
        "cpv_codes": act.cpv_codes,
        "nuts_codes": act.nuts_codes,
        "buyer_id": act.buyer_id,
        "buyer_name": act.buyer_name,
        "supplier_ids": act.supplier_ids,
        "supplier_names": act.supplier_names,
        "submission_date": act.submission_date.isoformat() if act.submission_date else None,
        "decision_date": act.decision_date.isoformat() if act.decision_date else None,
    }

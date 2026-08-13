"""Official publication links and evidence-bound tender summaries."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import quote

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_cpv_codes,
    act_identifiers,
    document_act_links,
    document_pages,
    documents,
    procurement_acts,
    source_records,
)

KHMDHS_PUBLIC_URL = "https://cerpp.eprocurement.gov.gr/upgkimdis/unprotected/home.xhtml"
KHMDHS_OPEN_DATA_URL = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata"
DIAVGEIA_URL = "https://diavgeia.gov.gr"
TED_URL = "https://ted.europa.eu"

_ADAM_RESOURCE = {
    "REQ": "request",
    "PROC": "notice",
    "AWRD": "auction",
    "SYMV": "contract",
    "PAY": "payment",
}
_ACT_PRIORITY = {
    "NOTICE": 0,
    "REQUEST": 1,
    "APPROVED_REQUEST": 2,
    "TED_NOTICE": 3,
    "AWARD": 4,
    "CONTRACT": 5,
    "AMENDMENT": 6,
    "DIAVGEIA_DECISION": 7,
    "PAYMENT": 8,
}
_ACT_LABEL = {
    "NOTICE": "προκήρυξη",
    "REQUEST": "αίτημα",
    "APPROVED_REQUEST": "εγκεκριμένο αίτημα",
    "TED_NOTICE": "ευρωπαϊκή προκήρυξη",
    "AWARD": "απόφαση ανάθεσης",
    "CONTRACT": "σύμβαση",
    "AMENDMENT": "τροποποίηση",
    "DIAVGEIA_DECISION": "απόφαση Διαύγειας",
    "PAYMENT": "πληρωμή",
}


def _adam_resource(identifier: str, resource_type: str | None = None) -> str | None:
    if resource_type in set(_ADAM_RESOURCE.values()):
        return resource_type
    match = re.match(r"^\d{2}(REQ|PROC|AWRD|SYMV|PAY)", identifier.upper())
    return _ADAM_RESOURCE.get(match.group(1)) if match else None


def official_links(
    *,
    source_system: str,
    resource_type: str | None,
    identifier_scheme: str,
    identifier: str,
) -> tuple[str | None, str | None]:
    """Return provider-owned record and primary-document URLs."""
    encoded = quote(identifier, safe="-")
    if identifier_scheme == "ADAM" or source_system == "KHMDHS":
        resource = _adam_resource(identifier, resource_type)
        official_url = f"{KHMDHS_PUBLIC_URL}?referenceNumber={encoded}"
        document_url = (
            f"{KHMDHS_OPEN_DATA_URL}/{resource}/attachment/{encoded}"
            if resource
            else None
        )
        return official_url, document_url
    if identifier_scheme == "ADA" or source_system == "DIAVGEIA":
        return (
            f"{DIAVGEIA_URL}/decision/view/{encoded}",
            f"{DIAVGEIA_URL}/doc/{encoded}",
        )
    if identifier_scheme == "TED_NOTICE_ID" or source_system == "TED":
        return (
            f"{TED_URL}/en/notice/-/detail/{encoded}",
            f"{TED_URL}/en/notice/{encoded}/pdf",
        )
    return None, None


def links_for_display_identifier(
    identifier_scheme: str | None,
    identifier: str | None,
) -> tuple[str | None, str | None]:
    if not identifier_scheme or not identifier:
        return None, None
    source = {
        "ADAM": "KHMDHS",
        "ADA": "DIAVGEIA",
        "TED_NOTICE_ID": "TED",
    }.get(identifier_scheme, "")
    return official_links(
        source_system=source,
        resource_type=None,
        identifier_scheme=identifier_scheme,
        identifier=identifier,
    )


def khmdhs_attachment_url(resource: str, adam: str) -> str:
    return f"{KHMDHS_OPEN_DATA_URL}/{resource}/attachment/{quote(adam, safe='-')}"


def _clean_text(value: Any, *, limit: int = 700) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _format_money(value: Decimal | float | int | str | None, currency: str | None) -> str | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} {currency or 'EUR'}"


def _format_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d/%m/%Y %H:%M") if isinstance(value, datetime) else value.strftime("%d/%m/%Y")


def _primary_identifier(
    identifiers: dict[str, list[str]],
    source_system: str,
) -> tuple[str | None, str | None]:
    preferred = {
        "KHMDHS": "ADAM",
        "DIAVGEIA": "ADA",
        "TED": "TED_NOTICE_ID",
    }.get(source_system)
    if preferred and identifiers.get(preferred):
        return preferred, identifiers[preferred][0]
    for scheme in ("ADAM", "ADA", "TED_NOTICE_ID"):
        if identifiers.get(scheme):
            return scheme, identifiers[scheme][0]
    return None, None


async def load_tender_publication_bundle(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID | None = None,
    act_id: uuid.UUID | None = None,
    buyer_name: str | None = None,
    fallback_title: str | None = None,
    locations: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    if process_id is None and act_id is None:
        raise ValueError("process_id or act_id is required")

    condition = (
        procurement_acts.c.process_id == process_id
        if process_id is not None
        else procurement_acts.c.id == act_id
    )
    act_rows = (
        await conn.execute(
            sa.select(
                procurement_acts,
                source_records.c.source_system,
                source_records.c.resource_type,
                source_records.c.source_native_id,
                source_records.c.fetched_at,
            )
            .join(source_records, source_records.c.id == procurement_acts.c.source_record_id)
            .where(condition)
        )
    ).mappings().all()
    act_ids = [row["id"] for row in act_rows]
    if not act_ids:
        return {
            "summary": _empty_summary(fallback_title),
            "official_records": [],
            "documents": [],
        }

    identifier_rows = (
        await conn.execute(
            sa.select(
                act_identifiers.c.act_id,
                act_identifiers.c.scheme,
                act_identifiers.c.value_normalized,
            ).where(act_identifiers.c.act_id.in_(act_ids))
        )
    ).all()
    identifiers_by_act: dict[uuid.UUID, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in identifier_rows:
        identifiers_by_act[row.act_id][row.scheme].append(row.value_normalized)

    cpv_rows = (
        await conn.execute(
            sa.select(act_cpv_codes.c.act_id, act_cpv_codes.c.cpv_code)
            .where(act_cpv_codes.c.act_id.in_(act_ids))
            .order_by(act_cpv_codes.c.is_primary.desc(), act_cpv_codes.c.cpv_code)
        )
    ).all()
    cpvs_by_act: dict[uuid.UUID, list[str]] = defaultdict(list)
    for row in cpv_rows:
        cpvs_by_act[row.act_id].append(row.cpv_code)

    official_records: list[dict[str, Any]] = []
    for row in act_rows:
        identifiers = dict(identifiers_by_act[row["id"]])
        scheme, identifier = _primary_identifier(identifiers, row["source_system"])
        official_url = document_url = None
        if scheme and identifier:
            official_url, document_url = official_links(
                source_system=row["source_system"],
                resource_type=row["resource_type"],
                identifier_scheme=scheme,
                identifier=identifier,
            )
        official_records.append({
            "act_id": str(row["id"]),
            "act_type": row["act_type"],
            "title": row["title"],
            "source_system": row["source_system"],
            "resource_type": row["resource_type"],
            "identifier_scheme": scheme,
            "identifier": identifier,
            "event_date": (
                row["publication_date"]
                or row["decision_date"]
                or row["submission_date"]
            ),
            "official_url": official_url,
            "document_url": document_url,
        })
    official_records.sort(
        key=lambda record: (
            _ACT_PRIORITY.get(record["act_type"], 99),
            -(
                record["event_date"].date().toordinal()
                if isinstance(record["event_date"], datetime)
                else record["event_date"].toordinal()
                if isinstance(record["event_date"], date)
                else 0
            ),
        )
    )

    document_rows = (
        await conn.execute(
            sa.select(
                documents,
                document_act_links.c.act_id.label("linked_act_id"),
                sa.func.coalesce(
                    document_act_links.c.document_type,
                    documents.c.document_type,
                ).label("linked_document_type"),
                sa.func.coalesce(
                    document_act_links.c.title,
                    documents.c.title,
                ).label("linked_title"),
                sa.func.coalesce(
                    document_act_links.c.source_url,
                    documents.c.source_url,
                ).label("linked_source_url"),
            )
            .join(
                document_act_links,
                document_act_links.c.document_id == documents.c.id,
            )
            .where(document_act_links.c.act_id.in_(act_ids))
            .order_by(documents.c.id, document_act_links.c.created_at)
            .distinct(documents.c.id)
        )
    ).mappings().all()
    document_ids = [row["id"] for row in document_rows]
    page_rows = []
    if document_ids:
        page_rows = (
            await conn.execute(
                sa.select(
                    document_pages.c.document_id,
                    document_pages.c.page_number,
                    sa.func.left(document_pages.c.text, 1200).label("text"),
                )
                .where(
                    document_pages.c.document_id.in_(document_ids),
                    document_pages.c.page_number <= 2,
                )
                .order_by(document_pages.c.document_id, document_pages.c.page_number)
            )
        ).all()
    page_text_by_document: dict[uuid.UUID, list[str]] = defaultdict(list)
    for row in page_rows:
        if row.text:
            page_text_by_document[row.document_id].append(row.text)

    document_items = []
    for row in document_rows:
        excerpt = _clean_text(
            " ".join(page_text_by_document.get(row["id"], [])),
            limit=900,
        )
        document_items.append({
            "document_id": str(row["id"]),
            "act_id": str(row["linked_act_id"]),
            "document_type": row["linked_document_type"],
            "title": row["linked_title"],
            "source_url": row["linked_source_url"],
            "object_uri": row["object_uri"],
            "mime_type": row["mime_type"],
            "file_size": row["file_size"],
            "text_extraction_status": row["text_extraction_status"],
            "page_count": row["page_count"],
            "language": row["language"],
            "excerpt": excerpt,
        })

    primary = min(
        act_rows,
        key=lambda row: (
            _ACT_PRIORITY.get(row["act_type"], 99),
            -int((row["publication_date"] or row["submission_date"] or date.min).strftime("%Y%m%d")),
        ),
    )
    summary = _build_summary(
        primary=primary,
        buyer_name=buyer_name,
        fallback_title=fallback_title,
        cpv_codes=cpvs_by_act.get(primary["id"], []),
        locations=locations,
        document_items=document_items,
    )
    return {
        "summary": summary,
        "official_records": official_records,
        "documents": document_items,
    }


def _empty_summary(title: str | None) -> dict[str, Any]:
    return {
        "text": _clean_text(title) or "Δεν υπάρχουν αρκετά δομημένα στοιχεία για σύνοψη.",
        "key_points": [],
        "document_excerpt": None,
        "methodology": "STRUCTURED_EXTRACTIVE",
        "primary_act_id": None,
    }


def _build_summary(
    *,
    primary: Any,
    buyer_name: str | None,
    fallback_title: str | None,
    cpv_codes: list[str],
    locations: Iterable[dict[str, Any]],
    document_items: list[dict[str, Any]],
) -> dict[str, Any]:
    title = _clean_text(primary["title"] or fallback_title) or "χωρίς καταγεγραμμένο τίτλο"
    act_label = _ACT_LABEL.get(primary["act_type"], "πράξη")
    subject = f"Η αναθέτουσα αρχή {buyer_name}" if buyer_name else "Η αναθέτουσα αρχή"
    sentences = [f"{subject} δημοσίευσε {act_label} με αντικείμενο «{title}»."]
    key_points: list[dict[str, Any]] = []

    details = primary["source_details"] or {}
    object_descriptions = [
        _clean_text(item.get("short_description"), limit=320)
        for item in details.get("object_details", [])
        if isinstance(item, dict)
    ]
    object_descriptions = [value for value in object_descriptions if value]
    if object_descriptions:
        sentences.append(f"Το δημοσιευμένο αντικείμενο περιλαμβάνει: {' · '.join(object_descriptions[:3])}.")

    amount = primary["amount_gross"] or primary["amount_net"]
    amount_text = _format_money(amount, primary["currency"])
    if amount_text:
        key_points.append({"label": "Καταγεγραμμένη αξία", "value": amount_text, "source": "CANONICAL_ACT"})
        sentences.append(f"Η καταγεγραμμένη αξία είναι {amount_text}.")

    deadline_text = _format_date(primary["submission_deadline"])
    if deadline_text:
        key_points.append({"label": "Προθεσμία προσφορών", "value": deadline_text, "source": "KHMDHS"})
        sentences.append(f"Η προθεσμία υποβολής προσφορών είναι {deadline_text}.")

    procedure = details.get("procedure_type") or primary["procedure_type"]
    if procedure:
        key_points.append({"label": "Διαδικασία", "value": str(procedure), "source": "KHMDHS"})
    if details.get("award_criterion"):
        key_points.append({"label": "Κριτήριο ανάθεσης", "value": str(details["award_criterion"]), "source": "KHMDHS"})
    if cpv_codes:
        key_points.append({"label": "CPV", "value": ", ".join(cpv_codes[:6]), "source": "CANONICAL_ACT"})

    location_names = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        value = (
            location.get("municipality_name")
            or location.get("place_text")
            or location.get("regional_unit_name")
            or location.get("region_name")
            or location.get("nuts_code")
        )
        if value and value not in location_names:
            location_names.append(str(value))
    if location_names:
        key_points.append({"label": "Τόπος εκτέλεσης", "value": ", ".join(location_names[:4]), "source": "ACT_LOCATION"})

    document_excerpt = next(
        (item["excerpt"] for item in document_items if item.get("excerpt")),
        None,
    )
    return {
        "text": " ".join(sentences),
        "key_points": key_points,
        "document_excerpt": document_excerpt,
        "methodology": "STRUCTURED_EXTRACTIVE",
        "primary_act_id": str(primary["id"]),
    }

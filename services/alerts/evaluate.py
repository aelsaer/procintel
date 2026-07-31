"""Alert rule evaluation — description.txt §32.

`evaluate_and_fire()` is fired from the ΚΗΜΔΗΣ pipeline's
`on_ingest_result` hook (wired in `connectors/khmdhs/cli.py`) after
`upsert_act` reports an insert or a material-field change (§32.3:
amount/status changed — the fields `db_writer.py::_MATERIAL_ACT_FIELDS`
tracks). Event type is derived from the act's own `act_type` via
`_EVENT_TYPES_BY_ACT_TYPE` — REQUEST/NOTICE produce `opportunity.*`
(pre-award tender activity), CONTRACT produces `contract.*`, PAYMENT
produces `payment.detected` (a single event type either way — §30.5 lists
no separate "payment.modified"). AWARD acts (the `auction` ΚΗΜΔΗΣ resource)
intentionally produce no event — nothing in §30.5's list maps to it.

Three more of §30.5's event types are produced elsewhere, not from this
act-upsert hook, since they're not procurement_acts-keyed events:
`evaluate_company_status_change_and_fire()` (called from
`connectors/khmdhs/cli.py` after `gemi/resolve.py::resolve_company_snapshot`
reports an actual status transition — a *different* field changing, e.g.
the registered office, does not fire this), `evaluate_buyer_new_procurement_and_fire()`
(called from `adamchain.py`'s process-*creation* branch — only when a
brand-new `procurement_processes` row is created for a buyer, not when an
existing one is extended), and `evaluate_expiring_contracts_and_fire()`
(NOT triggered by any upsert — a time-based scan over currently-active
contracts nearing their `end_date`; nothing in this codebase schedules
periodic jobs yet, so this needs to be invoked by an external cron-like
caller, consistent with "every run is a manual CLI invocation" elsewhere).

`alert.triggered` (§30.5's last event type) is deliberately not
implemented as its own evaluatable condition — it reads as the delivery
envelope concept itself (event ID, idempotency key, retry policy,
signature), which `DeliveryChannel.deliver()` already stands in for, not a
new source condition to detect.

Deduplication is enforced at the database level via `alert_events`' unique
index on (alert_rule_id, canonical_object_id, event_type,
material_change_hash) (§32.2) via INSERT ... ON CONFLICT DO NOTHING — so
calling any of the fire functions twice for the same (rule, object,
changed-fields) combination delivers at most once.

Filter matching (`rule_matches`) covers CPV prefixes, title keywords, NUTS,
municipality, buyer/supplier ids and amount ranges, aligned with the
business-profile shape used by the product.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import act_cpv_codes, act_locations, act_parties, alert_events, alert_rules, procurement_acts
from services.ingestion.connectors.khmdhs.db_writer import ActUpsertResult
from services.search_index.lexical import lexical_query_matches

from .delivery import DeliveryChannel

_EVENT_TYPES_BY_ACT_TYPE: dict[str, tuple[str, str]] = {
    # act_type -> (event type when is_new, event type when materially changed)
    "REQUEST": ("opportunity.created", "opportunity.updated"),
    "NOTICE": ("opportunity.created", "opportunity.updated"),
    "CONTRACT": ("contract.created", "contract.modified"),
    "PAYMENT": ("payment.detected", "payment.detected"),
}

EXPIRING_CONTRACT_WINDOW_DAYS = 30


def material_change_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value.casefold())
    accentless = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9α-ω]+", " ", accentless).strip()


def _keyword_matches(keyword: str, title: str) -> bool:
    return lexical_query_matches(keyword, title)


async def _fire_event(
    conn: AsyncConnection,
    *,
    delivery_channel: DeliveryChannel,
    event_type: str,
    canonical_object_type: str,
    canonical_object_id: uuid.UUID,
    context: dict[str, Any],
    change_payload: dict[str, Any],
) -> int:
    """Shared rule-matching + dedup-insert + deliver core used by every
    `evaluate_*_and_fire()` function below — the only thing that differs
    between `contract.created` and `company.status_changed` etc. is which
    object the event is about and what context `rule_matches()` sees, not
    the matching/dedup/delivery mechanics themselves."""
    rule_rows = (
        await conn.execute(
            select(alert_rules).where(
                alert_rules.c.is_active.is_(True),
                alert_rules.c.event_types.any(event_type),
            )
        )
    ).all()

    change_hash = material_change_hash(change_payload)
    fired_count = 0

    for rule in rule_rows:
        if not rule_matches(rule.filters or {}, context):
            continue

        insert_stmt = (
            pg_insert(alert_events)
            .values(
                id=uuid.uuid4(),
                alert_rule_id=rule.id,
                canonical_object_type=canonical_object_type,
                canonical_object_id=canonical_object_id,
                event_type=event_type,
                material_change_hash=change_hash,
                payload={**change_payload, "object_id": str(canonical_object_id)},
            )
            .on_conflict_do_nothing(
                index_elements=["alert_rule_id", "canonical_object_id", "event_type", "material_change_hash"]
            )
            .returning(alert_events.c.id)
        )
        newly_inserted = (await conn.execute(insert_stmt)).first()
        if newly_inserted is not None:
            fired_count += 1
            if rule.schedule == "IMMEDIATE":
                await delivery_channel.deliver(
                    conn,
                    alert_rule_id=rule.id,
                    tenant_id=rule.tenant_id,
                    alert_event_id=newly_inserted.id,
                    event_type=event_type,
                    payload={**change_payload, "object_id": str(canonical_object_id)},
                )

        await conn.execute(
            alert_rules.update()
            .where(alert_rules.c.id == rule.id)
            .values(last_evaluated_at=sa.func.now())
        )

    return fired_count


def rule_matches(filters: dict[str, Any], context: dict[str, Any]) -> bool:
    excluded_cpv_prefixes = [
        str(v)
        for v in (
            _as_list(filters.get("excluded_cpv_prefix"))
            + _as_list(filters.get("excluded_cpv_prefixes"))
        )
        if str(v).strip()
    ]
    if excluded_cpv_prefixes and any(
        str(code).startswith(prefix)
        for code in context.get("cpv_codes", [])
        for prefix in excluded_cpv_prefixes
    ):
        return False

    title = _normalize_text(context.get("title"))
    excluded_keywords = [
        _normalize_text(str(v))
        for v in (
            _as_list(filters.get("excluded_keyword"))
            + _as_list(filters.get("excluded_keywords"))
        )
        if str(v).strip()
    ]
    if any(_keyword_matches(keyword, title) for keyword in excluded_keywords):
        return False

    cpv_prefixes = [str(v) for v in (_as_list(filters.get("cpv_prefix")) + _as_list(filters.get("cpv_prefixes")))]
    cpv_matches = bool(cpv_prefixes) and any(
        str(c).startswith(prefix) for c in context.get("cpv_codes", []) for prefix in cpv_prefixes
    )

    nuts_filters = [str(v).upper() for v in (_as_list(filters.get("nuts_code")) + _as_list(filters.get("nuts_codes")))]
    if nuts_filters and not any(
        str(code).upper().startswith(prefix) for code in context.get("nuts_codes", []) for prefix in nuts_filters
    ):
        return False

    municipality = str(filters.get("municipality") or "").strip()
    if municipality and not any(
        lexical_query_matches(municipality, str(location))
        for location in context.get("location_names", [])
    ):
        return False

    keywords = [_normalize_text(str(v)) for v in (_as_list(filters.get("keyword")) + _as_list(filters.get("keywords")))]
    keyword_matches = bool(keywords) and any(_keyword_matches(keyword, title) for keyword in keywords)
    taxonomy_mode = str(filters.get("taxonomy_match_mode") or "").upper()
    if taxonomy_mode == "KEYWORD_REQUIRED":
        if keywords and not keyword_matches:
            return False
        if not keywords and cpv_prefixes and not cpv_matches:
            return False
    elif taxonomy_mode == "CPV_AND_KEYWORD":
        if cpv_prefixes and not cpv_matches:
            return False
        if keywords and not keyword_matches:
            return False
    elif filters.get("taxonomy_match_any"):
        if (cpv_prefixes or keywords) and not (cpv_matches or keyword_matches):
            return False
    elif cpv_prefixes and not cpv_matches:
        return False
    if keywords:
        if (
            taxonomy_mode not in {"CPV_AND_KEYWORD", "KEYWORD_REQUIRED"}
            and not filters.get("taxonomy_match_any")
            and not all(_keyword_matches(keyword, title) for keyword in keywords)
        ):
            return False

    buyer_id = filters.get("buyer_id")
    if buyer_id and buyer_id != context.get("buyer_id"):
        return False

    supplier_id = filters.get("supplier_id")
    if supplier_id and supplier_id != context.get("supplier_id"):
        return False

    amount_gross = context.get("amount_gross")
    amount_min = filters.get("amount_min")
    if amount_min is not None and (amount_gross is None or amount_gross < Decimal(str(amount_min))):
        return False

    amount_max = filters.get("amount_max")
    if amount_max is not None and (amount_gross is None or amount_gross > Decimal(str(amount_max))):
        return False

    return True


async def _load_act_context(conn: AsyncConnection, act_id: uuid.UUID) -> dict[str, Any]:
    act_row = (await conn.execute(select(procurement_acts).where(procurement_acts.c.id == act_id))).first()
    cpv_rows = (
        await conn.execute(select(act_cpv_codes.c.cpv_code).where(act_cpv_codes.c.act_id == act_id))
    ).all()
    location_rows = (
        await conn.execute(
            select(
                act_locations.c.nuts_code,
                act_locations.c.municipality_name,
                act_locations.c.place_text,
                act_locations.c.regional_unit_name,
                act_locations.c.region_name,
            ).where(
                act_locations.c.act_id == act_id,
            )
        )
    ).all()
    party_rows = (
        await conn.execute(
            select(act_parties.c.party_role, act_parties.c.entity_id).where(act_parties.c.act_id == act_id)
        )
    ).all()
    buyer_id = next(
        (str(r.entity_id) for r in party_rows if r.party_role in ("BUYER", "CONTRACTING_AUTHORITY")), None
    )
    supplier_id = next(
        (str(r.entity_id) for r in party_rows if r.party_role in ("SUPPLIER", "CONTRACTOR")), None
    )
    return {
        "cpv_codes": [row.cpv_code for row in cpv_rows],
        "nuts_codes": [row.nuts_code for row in location_rows if row.nuts_code],
        "location_names": list(dict.fromkeys(
            value
            for row in location_rows
            for value in (
                row.municipality_name,
                row.place_text,
                row.regional_unit_name,
                row.region_name,
            )
            if value
        )),
        "buyer_id": buyer_id,
        "supplier_id": supplier_id,
        "amount_gross": act_row.amount_gross if act_row is not None else None,
        "title": act_row.title if act_row is not None else None,
    }


async def evaluate_and_fire(
    conn: AsyncConnection,
    *,
    act_upsert: ActUpsertResult,
    delivery_channel: DeliveryChannel,
) -> int:
    """Returns how many alert_events rows were actually inserted (newly
    fired, not deduplicated). Event type depends on the act's own
    `act_type` — see `_EVENT_TYPES_BY_ACT_TYPE` and the module docstring."""
    event_types = _EVENT_TYPES_BY_ACT_TYPE.get(act_upsert.act_type)
    if event_types is None:
        return 0  # this act_type has no §30.5 event mapping (e.g. AWARD)
    created_event, modified_event = event_types

    if act_upsert.is_new:
        event_type = created_event
        change_payload: dict[str, Any] = {"created": True}
    elif act_upsert.changed_fields:
        event_type = modified_event
        change_payload = {k: [str(old), str(new)] for k, (old, new) in act_upsert.changed_fields.items()}
    else:
        return 0  # no material change — not an event

    context = await _load_act_context(conn, act_upsert.act_id)
    change_payload["title"] = context["title"]

    fired_count = await _fire_event(
        conn,
        delivery_channel=delivery_channel,
        event_type=event_type,
        canonical_object_type="procurement_acts",
        canonical_object_id=act_upsert.act_id,
        context=context,
        change_payload=change_payload,
    )
    await conn.commit()
    return fired_count


async def evaluate_company_status_change_and_fire(
    conn: AsyncConnection,
    *,
    entity_id: uuid.UUID,
    old_status: str | None,
    new_status: str | None,
    delivery_channel: DeliveryChannel,
) -> int:
    """`company.status_changed` (§30.5) — called from
    `connectors/khmdhs/cli.py` after `gemi/resolve.py::resolve_company_snapshot`
    writes a new snapshot; fires only when the status itself actually
    differs (a new snapshot can be written for other reasons — official
    name, registered office, ... — that aren't a status change). A
    brand-new company (`old_status is None`) is not a "change", so this is
    a no-op in that case too — there's nothing to compare against."""
    if old_status is None or new_status is None or old_status == new_status:
        return 0

    context = {"buyer_id": None, "supplier_id": str(entity_id), "cpv_codes": [], "amount_gross": None}
    change_payload = {"old_status": old_status, "new_status": new_status}

    fired_count = await _fire_event(
        conn,
        delivery_channel=delivery_channel,
        event_type="company.status_changed",
        canonical_object_type="entities",
        canonical_object_id=entity_id,
        context=context,
        change_payload=change_payload,
    )
    await conn.commit()
    return fired_count


async def evaluate_buyer_new_procurement_and_fire(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID,
    buyer_entity_id: uuid.UUID,
    delivery_channel: DeliveryChannel,
) -> int:
    """`buyer.new_procurement` (§30.5) — called from `adamchain.py`'s
    process-*creation* branch only (a brand-new `procurement_processes`
    row for this buyer), never when an existing process is merely extended
    with another act. Filter matching only really uses `buyer_id` here —
    there's no CPV/amount/supplier yet for a process this new."""
    context = {"buyer_id": str(buyer_entity_id), "supplier_id": None, "cpv_codes": [], "amount_gross": None}
    change_payload = {"created": True}

    fired_count = await _fire_event(
        conn,
        delivery_channel=delivery_channel,
        event_type="buyer.new_procurement",
        canonical_object_type="procurement_processes",
        canonical_object_id=process_id,
        context=context,
        change_payload=change_payload,
    )
    await conn.commit()
    return fired_count


async def evaluate_expiring_contracts_and_fire(
    conn: AsyncConnection,
    *,
    delivery_channel: DeliveryChannel,
    as_of: date,
    window_days: int = EXPIRING_CONTRACT_WINDOW_DAYS,
) -> int:
    """`contract.expiring` (§30.5) — the one event type in this module that
    is **not** triggered by an ingestion upsert: nothing here fires it
    automatically, it needs a periodic caller (a cron-like scheduler, none
    of which exists in this codebase yet — every other job here is a
    manual CLI invocation, and this is no different). Scans
    `procurement_acts` for currently-active CONTRACT acts whose `end_date`
    falls within `[as_of, as_of + window_days]` — each qualifying contract
    fires at most once per day (`material_change_hash` includes `as_of`,
    so re-running this scan daily against the same still-expiring contract
    produces a new, distinct event each day rather than one that's
    deduplicated away — a contract 25 days from expiry is worth a fresh
    reminder closer to the deadline, not silence after the first alert)."""
    window_end = as_of + timedelta(days=window_days)

    rows = (
        await conn.execute(
            select(procurement_acts).where(
                procurement_acts.c.act_type == "CONTRACT",
                procurement_acts.c.is_current.is_(True),
                procurement_acts.c.end_date.is_not(None),
                procurement_acts.c.end_date >= as_of,
                procurement_acts.c.end_date <= window_end,
            )
        )
    ).all()

    total_fired = 0
    for act_row in rows:
        context = await _load_act_context(conn, act_row.id)
        change_payload = {
            "title": context["title"],
            "end_date": act_row.end_date.isoformat(),
            "as_of": as_of.isoformat(),
        }
        total_fired += await _fire_event(
            conn,
            delivery_channel=delivery_channel,
            event_type="contract.expiring",
            canonical_object_type="procurement_acts",
            canonical_object_id=act_row.id,
            context=context,
            change_payload=change_payload,
        )

    await conn.commit()
    return total_fired

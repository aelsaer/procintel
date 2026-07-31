"""TED <-> ΚΗΜΔΗΣ linkage — description.txt §21.3's join hierarchy.

**Level 3** (buyer VAT + publication date proximity + CPV) and **Level 4**
(buyer + title + amount + date, no CPV requirement) are implemented.
Levels 1-2 need a TED notice ID or explicit document reference already
present on the ΚΗΜΔΗΣ side — nothing in ΚΗΜΔΗΣ's preserved field list
(§16) carries one, so those levels have nothing to trigger from; they'd
only become reachable once the documents pipeline can find a TED reference
inside a contract document. Level 5 (manual review) has no dedicated
queue/UI — see Level 4's note below on how "review" is represented here.

Direction here is the mirror image of `connectors/diavgeia` and
`connectors/anaptyxi`: those are triggered *from* a ΚΗΜΔΗΣ act pointing at
Διαύγεια/ΑΝΑΠΤΥΞΗ; TED notices are ingested independently (their own
backfill, `pipeline.py`) and this module then searches *from* the TED side
for a matching ΚΗΜΔΗΣ process — because nothing on the ΚΗΜΔΗΣ side names the
TED notice up front.

Level 3 is tried first (same buyer + CPV overlap + date proximity,
confidence 0.85); if it finds zero or multiple candidate processes, Level 4
is tried as a fallback against the **same buyer** but without requiring any
CPV overlap — title similarity (`services/entity_resolution/
text_similarity.py`) + amount tolerance instead (confidence 0.65, lower
than Level 3 since it's a strictly weaker signal). Level 4 links get
`act_links.reviewed_by IS NULL` — the same review-queue signal
`act_links` already supports and `connectors/anaptyxi` now uses too
(querying `act_links WHERE confidence < 0.85 AND reviewed_by IS NULL`
doubles as the review queue; no separate table was built for this).

Matching is deliberately conservative at every level: linked only when the
candidate query returns **exactly one** distinct process. Zero or multiple
candidates means "not confident enough" — left unlinked rather than
guessed at, consistent with the rest of this codebase's matching hierarchy
discipline (§8: never auto-merge on weak signal).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    act_cpv_codes,
    act_links,
    act_parties,
    procurement_acts,
    process_members,
    procurement_processes,
)
from services.entity_resolution.text_similarity import normalized_similarity

DATE_PROXIMITY_WINDOW_DAYS = 180
MATCH_CONFIDENCE = 0.85  # multi-attribute match (§8 level 3), not a source-asserted relationship
LEVEL4_MATCH_CONFIDENCE = 0.65  # weaker than Level 3 — no CPV requirement, needs review
LEVEL4_TITLE_SIMILARITY_THRESHOLD = 0.5
LEVEL4_AMOUNT_TOLERANCE = 0.15  # +/-15%


def _buyer_match_clause(buyer_entity_id: uuid.UUID):
    """Use the process summary when present, with canonical act parties as fallback."""
    buyer_act = procurement_acts.alias("buyer_match_act")
    buyer_party = act_parties.alias("buyer_match_party")
    process_has_buyer_party = (
        select(sa.literal(1))
        .select_from(buyer_act.join(buyer_party, buyer_party.c.act_id == buyer_act.c.id))
        .where(
            buyer_act.c.process_id == procurement_processes.c.id,
            buyer_party.c.entity_id == buyer_entity_id,
            buyer_party.c.party_role.in_(("BUYER", "CONTRACTING_AUTHORITY")),
        )
        .exists()
    )
    return sa.or_(
        procurement_processes.c.buyer_entity_id == buyer_entity_id,
        process_has_buyer_party,
    )


async def _find_candidate_process(
    conn: AsyncConnection,
    *,
    buyer_entity_id: uuid.UUID,
    cpv_codes: list[str],
    publication_date: date,
) -> uuid.UUID | None:
    if not cpv_codes:
        return None

    window_start = publication_date - timedelta(days=DATE_PROXIMITY_WINDOW_DAYS)
    window_end = publication_date + timedelta(days=DATE_PROXIMITY_WINDOW_DAYS)
    act_date = func.coalesce(
        procurement_acts.c.publication_date,
        procurement_acts.c.submission_date,
        procurement_acts.c.decision_date,
    )

    rows = (
        await conn.execute(
            select(procurement_processes.c.id)
            .distinct()
            .select_from(
                procurement_processes.join(
                    procurement_acts, procurement_acts.c.process_id == procurement_processes.c.id
                ).join(act_cpv_codes, act_cpv_codes.c.act_id == procurement_acts.c.id)
            )
            .where(
                _buyer_match_clause(buyer_entity_id),
                procurement_processes.c.record_status == "ACTIVE",
                act_cpv_codes.c.cpv_code.in_(cpv_codes),
                act_date.is_not(None),
                act_date >= window_start,
                act_date <= window_end,
            )
        )
    ).all()

    distinct_ids = {row.id for row in rows}
    if len(distinct_ids) != 1:
        return None
    return next(iter(distinct_ids))


async def _find_candidate_process_by_title_amount(
    conn: AsyncConnection,
    *,
    buyer_entity_id: uuid.UUID,
    title: str | None,
    amount: Decimal | None,
    publication_date: date,
) -> uuid.UUID | None:
    """Level 4 fallback: same buyer, no CPV requirement — title similarity
    + amount tolerance instead, over the same date-proximity window."""
    if not title:
        return None

    window_start = publication_date - timedelta(days=DATE_PROXIMITY_WINDOW_DAYS)
    window_end = publication_date + timedelta(days=DATE_PROXIMITY_WINDOW_DAYS)
    act_date = func.coalesce(
        procurement_acts.c.publication_date,
        procurement_acts.c.submission_date,
        procurement_acts.c.decision_date,
    )

    rows = (
        await conn.execute(
            select(procurement_acts.c.process_id, procurement_acts.c.title, procurement_acts.c.amount_gross)
            .select_from(
                procurement_processes.join(
                    procurement_acts, procurement_acts.c.process_id == procurement_processes.c.id
                )
            )
            .where(
                _buyer_match_clause(buyer_entity_id),
                procurement_processes.c.record_status == "ACTIVE",
                act_date.is_not(None),
                act_date >= window_start,
                act_date <= window_end,
            )
        )
    ).all()

    matched_processes: set[uuid.UUID] = set()
    for row in rows:
        title_score = normalized_similarity(row.title, title)
        if title_score < LEVEL4_TITLE_SIMILARITY_THRESHOLD:
            continue
        if amount is not None and row.amount_gross is not None:
            tolerance = amount * Decimal(str(LEVEL4_AMOUNT_TOLERANCE))
            if abs(Decimal(row.amount_gross) - amount) > tolerance:
                continue
        matched_processes.add(row.process_id)

    if len(matched_processes) != 1:
        return None
    return next(iter(matched_processes))


async def _pick_representative_act(conn: AsyncConnection, *, process_id: uuid.UUID) -> uuid.UUID | None:
    preferred = (
        await conn.execute(
            select(procurement_acts.c.id)
            .where(
                procurement_acts.c.process_id == process_id,
                procurement_acts.c.act_type.in_(("CONTRACT", "AWARD")),
            )
            .order_by(procurement_acts.c.publication_date.asc().nulls_last())
            .limit(1)
        )
    ).first()
    if preferred is not None:
        return preferred.id

    fallback = (
        await conn.execute(select(procurement_acts.c.id).where(procurement_acts.c.process_id == process_id).limit(1))
    ).first()
    return fallback.id if fallback is not None else None


async def _join_process(conn: AsyncConnection, *, process_id: uuid.UUID, act_id: uuid.UUID) -> None:
    already_member = (
        await conn.execute(
            select(process_members.c.id).where(
                process_members.c.process_id == process_id, process_members.c.act_id == act_id
            )
        )
    ).first()
    if already_member is None:
        await conn.execute(
            process_members.insert().values(
                id=uuid.uuid4(), process_id=process_id, act_id=act_id, added_via="LINKAGE_ENGINE"
            )
        )
    # keep the denormalized pointer db/marts/procurement_360.sql reads in sync
    # (same fix applied to adamchain.py and diavgeia/resolve.py)
    await conn.execute(procurement_acts.update().where(procurement_acts.c.id == act_id).values(process_id=process_id))


async def resolve_notice_process_link(
    conn: AsyncConnection,
    *,
    ted_act_id: uuid.UUID,
    buyer_entity_id: uuid.UUID | None,
    cpv_codes: list[str],
    publication_date: date | None,
    title: str | None = None,
    amount: Decimal | None = None,
) -> uuid.UUID | None:
    """Returns the matched process_id, or None if no confident match was
    found (not an error — most TED notices for a Greek buyer that doesn't
    also happen to run a matching ΚΗΜΔΗΣ process in the same window simply
    won't match, and that's correct). Tries Level 3 (buyer + CPV + date)
    first; if that finds zero or multiple candidates, falls back to Level 4
    (buyer + title + amount + date, no CPV requirement) when `title` is
    given."""
    if buyer_entity_id is None or publication_date is None:
        return None

    link_method = "BUYER_VAT_CPV_DATE_PROXIMITY"
    confidence: float = MATCH_CONFIDENCE
    evidence: dict = {
        "cpv_codes": cpv_codes,
        "publication_date": publication_date.isoformat(),
        "window_days": DATE_PROXIMITY_WINDOW_DAYS,
    }

    process_id = await _find_candidate_process(
        conn, buyer_entity_id=buyer_entity_id, cpv_codes=cpv_codes, publication_date=publication_date
    )
    if process_id is None:
        process_id = await _find_candidate_process_by_title_amount(
            conn, buyer_entity_id=buyer_entity_id, title=title, amount=amount, publication_date=publication_date
        )
        if process_id is None:
            return None
        link_method = "BUYER_TITLE_AMOUNT_DATE"
        confidence = LEVEL4_MATCH_CONFIDENCE
        evidence = {
            "title": title,
            "amount": str(amount) if amount is not None else None,
            "publication_date": publication_date.isoformat(),
            "window_days": DATE_PROXIMITY_WINDOW_DAYS,
            "needs_review": True,
        }

    representative_act_id = await _pick_representative_act(conn, process_id=process_id)
    if representative_act_id is None:
        return None

    already_linked = (
        await conn.execute(
            select(act_links.c.id).where(
                act_links.c.from_act_id == ted_act_id,
                act_links.c.to_act_id == representative_act_id,
                act_links.c.link_type == "PUBLISHED_AS",
            )
        )
    ).first()
    if already_linked is None:
        await conn.execute(
            act_links.insert().values(
                id=uuid.uuid4(),
                from_act_id=ted_act_id,
                to_act_id=representative_act_id,
                link_type="PUBLISHED_AS",
                link_method=link_method,
                confidence=confidence,
                evidence=evidence,
                created_by="services.ingestion.connectors.ted.resolve",
            )
        )

    await _join_process(conn, process_id=process_id, act_id=ted_act_id)
    return process_id

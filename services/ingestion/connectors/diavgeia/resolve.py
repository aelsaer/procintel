"""Διαύγεια decision resolution — description.txt §17.1, §17.4.

Called whenever a new ΑΔΑ appears in ΚΗΜΔΗΣ data (§17.1's job trigger,
`ActUpsertResult.related_ada` — see
`services/ingestion/connectors/khmdhs/db_writer.py`'s module docstring for
why the ΑΔΑ isn't stored as an identifier of the ΚΗΜΔΗΣ act itself): fetches
the decision by direct ΑΔΑ lookup, stores it as its own act, and links it to
the originating ΚΗΜΔΗΣ act with confidence 1.0 (`EXACT_ADA`, §17.4). If the
originating act already belongs to a process, the decision act joins the
same process — a Διαύγεια decision is lifecycle evidence for an existing
procurement, not a separate one.

`resolve_decision_via_search()` implements the §17.4 fallback tier ("search
by title or organization") for when DIRECT_ADA_FETCH finds nothing — its
own circuit breaker (`client.py`) keeps a degraded SEARCH capability from
ever blocking direct fetch, per §17.3. When basic SEARCH returns zero or
multiple candidates and the caller has a `decision_type`/`protocol_number`
to narrow with, one ADVANCED_SEARCH retry is attempted before giving up —
its own circuit breaker too, so a degraded ADVANCED_SEARCH never blocks
SEARCH or direct fetch either. `ORGANIZATION_LOOKUP`/`SIGNER_LOOKUP`/
`VERSION_LOG` remain unimplemented.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import act_identifiers, act_links, procurement_acts, process_members
from packages.source_clients.raw_store import RawStore
from services.documents.pipeline import process_document
from services.entity_resolution.text_similarity import normalized_similarity

from .client import DecisionNotFoundError, DiavgeiaClient
from .db_writer import DecisionIngestResult, ingest_decision_record
from .normalize import normalize_ada

SEARCH_MATCH_CONFIDENCE = 0.75
SEARCH_ORG_SIMILARITY_THRESHOLD = 0.6
SEARCH_TITLE_SIMILARITY_THRESHOLD = 0.5

_logger = logging.getLogger("procintel.diavgeia.resolve")


async def _maybe_process_decision_document(
    conn: AsyncConnection, *, result: DecisionIngestResult, process_documents: bool
) -> None:
    """§23's documents pipeline was fully built with no automatic caller —
    this is that caller. Opt-in (mirrors every other "needs its own real
    infra to run well" enrichment in this codebase, e.g. ΓΕΜΗ/OpenSearch):
    downloading, antivirus-scanning, and OCRing an arbitrary government PDF
    is a heavier and slower operation than the API-response fetches this
    module otherwise does, and shouldn't happen by default on a large
    historical backfill. A failure here (a broken URL, an oversized file,
    an OCR timeout) is logged and never raised — the decision itself is
    already fully linked/stored by the time this runs, and losing that over
    a failed PDF download would be strictly worse than a missing document."""
    if not process_documents or not result.document_url or result.act_id is None:
        return
    try:
        await process_document(
            conn,
            url=result.document_url,
            act_id=result.act_id,
            document_type="DIAVGEIA_DECISION_PDF",
        )
    except Exception:  # noqa: BLE001 — see docstring above
        _logger.exception("failed to process Διαύγεια decision document for act %s", result.act_id)


async def _link_to_origin(
    conn: AsyncConnection,
    *,
    origin_act_id: uuid.UUID,
    decision_act_id: uuid.UUID,
    link_method: str = "EXACT_ADA",
    confidence: float = 1.000,
    evidence: dict | None = None,
) -> None:
    if origin_act_id == decision_act_id:
        return
    already_linked = (
        await conn.execute(
            select(act_links.c.id).where(
                act_links.c.from_act_id == decision_act_id,
                act_links.c.to_act_id == origin_act_id,
                act_links.c.link_type == "APPROVES",
            )
        )
    ).first()
    if already_linked is not None:
        return
    await conn.execute(
        act_links.insert().values(
            id=uuid.uuid4(),
            from_act_id=decision_act_id,
            to_act_id=origin_act_id,
            link_type="APPROVES",
            link_method=link_method,
            confidence=confidence,
            evidence=evidence or {"source": "DIAVGEIA", "method": "direct_ada_fetch"},
            created_by="services.ingestion.connectors.diavgeia.resolve",
        )
    )


async def _join_origin_process(
    conn: AsyncConnection, *, origin_act_id: uuid.UUID, decision_act_id: uuid.UUID
) -> None:
    origin_membership = (
        await conn.execute(select(process_members.c.process_id).where(process_members.c.act_id == origin_act_id))
    ).first()
    if origin_membership is None:
        return  # origin act not yet grouped into a process (adamChain hasn't run) — nothing to join

    already_member = (
        await conn.execute(
            select(process_members.c.id).where(
                process_members.c.process_id == origin_membership.process_id,
                process_members.c.act_id == decision_act_id,
            )
        )
    ).first()
    if already_member is None:
        await conn.execute(
            process_members.insert().values(
                id=uuid.uuid4(),
                process_id=origin_membership.process_id,
                act_id=decision_act_id,
                added_via="LINKAGE_ENGINE",
            )
        )
    # process_members is the audit trail; procurement_acts.process_id is the
    # denormalized pointer db/marts/procurement_360.sql actually reads (same
    # fix applied to services/ingestion/connectors/khmdhs/adamchain.py).
    await conn.execute(
        procurement_acts.update()
        .where(procurement_acts.c.id == decision_act_id)
        .values(process_id=origin_membership.process_id)
    )


async def link_existing_decision_for_ada(
    conn: AsyncConnection,
    *,
    ada: str,
    origin_act_id: uuid.UUID,
) -> uuid.UUID | None:
    """Link an already ingested Diavgeia decision without another API call."""
    ada_normalized = normalize_ada(ada)
    decision_act_id = (
        await conn.execute(
            select(act_identifiers.c.act_id)
            .where(
                act_identifiers.c.scheme == "ADA",
                act_identifiers.c.value_normalized == ada_normalized,
            )
            .limit(1)
        )
    ).scalar()
    if decision_act_id is None or decision_act_id == origin_act_id:
        return None

    await _link_to_origin(
        conn,
        origin_act_id=origin_act_id,
        decision_act_id=decision_act_id,
        evidence={"source": "DIAVGEIA", "method": "existing_exact_ada"},
    )
    await _join_origin_process(conn, origin_act_id=origin_act_id, decision_act_id=decision_act_id)
    await conn.commit()
    return decision_act_id


async def resolve_decision_for_ada(
    conn: AsyncConnection,
    *,
    client: DiavgeiaClient,
    raw_store: RawStore,
    ada: str,
    origin_act_id: uuid.UUID,
    process_documents: bool = False,
) -> uuid.UUID | None:
    """Direct-ΑΔΑ fetch + store + link. Returns the decision act_id, or
    None if Διαύγεια has no decision for this ΑΔΑ — not an error; the ΑΔΑ
    may not (yet) resolve to a published decision. `process_documents`
    (opt-in, off by default) additionally downloads/OCRs the decision's own
    PDF via `services/documents/pipeline.py::process_document()` — see
    `_maybe_process_decision_document`'s docstring."""
    ada_normalized = normalize_ada(ada)

    try:
        response = await client.fetch_decision_by_ada(ada_normalized)
    except DecisionNotFoundError:
        return None

    raw_ref = await raw_store.put(
        source="diavgeia",
        resource="decision",
        partition_key=f"ada={ada_normalized}",
        payload=response.raw_body,
    )

    result = await ingest_decision_record(
        conn,
        ada=ada_normalized,
        raw_body=response.body,
        payload_uri=raw_ref.payload_uri,
        content_sha256=raw_ref.content_sha256,
        http_status=response.http_status,
        fetched_at=datetime.now(timezone.utc),
    )

    if result.act_id is None:
        return None

    await _link_to_origin(conn, origin_act_id=origin_act_id, decision_act_id=result.act_id)
    await _join_origin_process(conn, origin_act_id=origin_act_id, decision_act_id=result.act_id)
    await conn.commit()
    await _maybe_process_decision_document(conn, result=result, process_documents=process_documents)
    return result.act_id


def _score_search_results(
    results: list[dict], *, organization_query: str, title_query: str
) -> list[tuple[float, float, dict]]:
    scored: list[tuple[float, float, dict]] = []
    for raw_result in results:
        org_label = raw_result.get("organizationLabel") or raw_result.get("issuingAuthority")
        subject = raw_result.get("subject") or raw_result.get("θέμα")
        org_score = normalized_similarity(org_label, organization_query)
        title_score = normalized_similarity(subject, title_query)
        if org_score >= SEARCH_ORG_SIMILARITY_THRESHOLD and title_score >= SEARCH_TITLE_SIMILARITY_THRESHOLD:
            scored.append((org_score, title_score, raw_result))
    return scored


async def resolve_decision_via_search(
    conn: AsyncConnection,
    *,
    client: DiavgeiaClient,
    raw_store: RawStore,
    origin_act_id: uuid.UUID,
    organization_query: str,
    title_query: str,
    decision_type: str | None = None,
    protocol_number: str | None = None,
    process_documents: bool = False,
) -> uuid.UUID | None:
    """§17.4's fallback linkage tier ("search by title or organization") —
    used when DIRECT_ADA_FETCH found nothing for any ΑΔΑ the origin act
    references (or none was referenced at all). Per §17.4, this tier gets
    `confidence < 1.0` and `link_method = DIAVGEIA_SEARCH_MATCH`, and
    requires "multiple confirming evidence pieces" — both an
    organization-label match and a title match above threshold, on a
    single unambiguous best candidate. Zero or more-than-one plausible
    candidate is left unlinked, same discipline as every matching tier in
    this codebase (§8: never guess on weak or ambiguous signal) — *unless*
    the caller passed `decision_type`/`protocol_number`, in which case a
    zero-or-ambiguous basic SEARCH result gets one ADVANCED_SEARCH retry
    with those extra filters added, before giving up (ADVANCED_SEARCH is a
    disambiguation narrower here, not an independent tier — §17.3 only
    describes one linkage confidence for "search by title or organization").

    A SEARCH failure (degraded/unavailable, §17.3) returns None rather
    than raising — this is always an optional enrichment attempt, never
    something that should break the caller's ingestion loop.
    """
    try:
        response = await client.search_decisions(organization_query=organization_query, title_query=title_query)
    except Exception:
        return None

    scored = _score_search_results(response.results, organization_query=organization_query, title_query=title_query)

    if len(scored) != 1 and (decision_type or protocol_number):
        try:
            advanced_response = await client.search_decisions_advanced(
                organization_query=organization_query,
                title_query=title_query,
                decision_type=decision_type,
                protocol_number=protocol_number,
            )
        except Exception:
            advanced_response = None
        if advanced_response is not None:
            scored = _score_search_results(
                advanced_response.results, organization_query=organization_query, title_query=title_query
            )

    if len(scored) != 1:
        return None  # zero or ambiguous multiple candidates — not confident enough

    org_score, title_score, raw_result = scored[0]
    ada = raw_result.get("ada") or raw_result.get("ADA")
    if not ada:
        return None
    ada_normalized = normalize_ada(ada)

    try:
        full_response = await client.fetch_decision_by_ada(ada_normalized)
    except DecisionNotFoundError:
        return None  # search result didn't resolve to a real record on re-fetch — inconsistent source, not a crash

    raw_ref = await raw_store.put(
        source="diavgeia",
        resource="decision",
        partition_key=f"ada={ada_normalized}",
        payload=full_response.raw_body,
    )
    result = await ingest_decision_record(
        conn,
        ada=ada_normalized,
        raw_body=full_response.body,
        payload_uri=raw_ref.payload_uri,
        content_sha256=raw_ref.content_sha256,
        http_status=full_response.http_status,
        fetched_at=datetime.now(timezone.utc),
    )
    if result.act_id is None:
        return None

    await _link_to_origin(
        conn,
        origin_act_id=origin_act_id,
        decision_act_id=result.act_id,
        link_method="DIAVGEIA_SEARCH_MATCH",
        confidence=SEARCH_MATCH_CONFIDENCE,
        evidence={
            "source": "DIAVGEIA",
            "method": "search",
            "organization_similarity": round(org_score, 4),
            "title_similarity": round(title_score, 4),
        },
    )
    await _join_origin_process(conn, origin_act_id=origin_act_id, decision_act_id=result.act_id)
    await conn.commit()
    await _maybe_process_decision_document(conn, result=result, process_documents=process_documents)
    return result.act_id

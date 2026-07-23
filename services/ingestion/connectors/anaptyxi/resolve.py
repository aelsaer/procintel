"""ΑΝΑΠΤΥΞΗ funding-link resolution — description.txt §19.2's join hierarchy.

    1. Exact ΟΠΣ/MIS code — only when the source field's semantics are
       confirmed for that record type. Confidence 0.95 (`MIS_OPS_EXACT`).
    2. Beneficiary/contractor ΑΦΜ + project title + time period.
       Confidence 0.85 (`AFM_TITLE_PERIOD`).
    3. ΑΔΑ or ΑΔΑΜ found in metadata or documents. Confidence 0.90
       (`ADA_ADAM_IN_METADATA`) — a concrete identifier match, stronger
       than fuzzy text but still not source-asserted (contrast with
       `adamChain`/Διαύγεια exact-ΑΔΑ links at 1.0).
    4. Normalized title + similar amount + same region + same beneficiary,
       with mandatory review when confidence isn't high. Confidence 0.60
       (`FUZZY_TITLE_AMOUNT_REGION`), left with `funding_links.reviewed_by
       IS NULL` — the review-queue signal (§19.2's own words, mirroring
       the same confidence + `reviewed_by IS NULL` convention `act_links`
       already uses; no separate review-queue table exists).

Tried strictly in that order — the first level that produces exactly one
unambiguous candidate wins; a ΚΗΜΔΗΣ act whose funding-reference fields
don't resolve at any level is left unlinked rather than guessed at (§8:
never guess on weak or ambiguous signal).

Levels 2-4 share one beneficiary/contractor-ΑΦΜ-scoped candidate set
(`AnaptyxiClient.find_projects_by_beneficiary_afm`) rather than three
separate searches — Level 2 scores by title+period, Level 3 by ΑΔΑ/ΑΔΑΜ
text containment, Level 4 by a looser title+amount threshold, all against
the same fetched candidates, in that priority order. This is a scoping
simplification given ΑΝΑΠΤΥΞΗ's Open Data API doesn't (per description.txt)
expose a general full-text/region search — "same region" in Level 4 is
therefore checked only when both sides happen to carry a NUTS code, never
required.

**The §19.4 "critical correction" in practice**: ΚΗΜΔΗΣ carries two
candidate funding-reference fields (`publicFundingRefOps`,
`espaFundProgramRef`) and neither is assumed to be the ΟΠΣ/MIS code without
evidence. Both are tried as candidate MIS values at Level 1; whichever one
actually resolves to a real ΑΝΑΠΤΥΞΗ project is recorded in
`funding_links.evidence` so it's clear after the fact which field's
semantics were confirmed for that record.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import funding_links, funding_projects
from packages.source_clients.raw_store import RawStore
from services.entity_resolution.text_similarity import normalized_similarity

from .client import AnaptyxiBeneficiarySearchResponse, AnaptyxiClient, ProjectNotFoundError
from .db_writer import ingest_project_record

FUNDING_REF_EXACT_MIS_CONFIDENCE = 0.95
LEVEL2_CONFIDENCE = 0.85
LEVEL2_TITLE_SIMILARITY_THRESHOLD = 0.5
LEVEL2_PERIOD_SLACK_DAYS = 60
LEVEL3_CONFIDENCE = 0.90
LEVEL4_CONFIDENCE = 0.60
LEVEL4_TITLE_SIMILARITY_THRESHOLD = 0.35
LEVEL4_AMOUNT_TOLERANCE = 0.15  # +/-15%


async def _link_exists(conn: AsyncConnection, *, act_id: uuid.UUID, funding_project_id: uuid.UUID) -> bool:
    row = (
        await conn.execute(
            select(funding_links.c.id).where(
                funding_links.c.act_id == act_id,
                funding_links.c.funding_project_id == funding_project_id,
            )
        )
    ).first()
    return row is not None


async def _find_project_id(conn: AsyncConnection, *, mis_value: str, program_period: str) -> uuid.UUID | None:
    row = (
        await conn.execute(
            select(funding_projects.c.id).where(
                funding_projects.c.mis_ops_code == mis_value,
                funding_projects.c.program_period == program_period,
            )
        )
    ).first()
    return row.id if row is not None else None


async def _create_link(
    conn: AsyncConnection,
    *,
    act_id: uuid.UUID,
    funding_project_id: uuid.UUID,
    link_method: str,
    confidence: float,
    evidence: dict[str, Any],
) -> uuid.UUID:
    if not await _link_exists(conn, act_id=act_id, funding_project_id=funding_project_id):
        await conn.execute(
            funding_links.insert().values(
                id=uuid.uuid4(),
                act_id=act_id,
                funding_project_id=funding_project_id,
                link_method=link_method,
                confidence=confidence,
                evidence=evidence,
            )
        )
    await conn.commit()
    return funding_project_id


async def _ingest_candidate_and_link(
    conn: AsyncConnection,
    *,
    raw_store: RawStore,
    program_period: str,
    act_id: uuid.UUID,
    raw_project: dict[str, Any],
    link_method: str,
    confidence: float,
    evidence: dict[str, Any],
) -> uuid.UUID | None:
    mis_code = raw_project.get("misCode") or raw_project.get("mis") or raw_project.get("opsCode")
    if not mis_code:
        return None  # can't ingest a project with no identifier at all

    raw_ref = await raw_store.put(
        source="anaptyxi",
        resource=program_period,
        partition_key=f"mis={mis_code}",
        payload=json.dumps(raw_project, ensure_ascii=False).encode("utf-8"),
    )
    ingest_result = await ingest_project_record(
        conn,
        mis_code=mis_code,
        program_period=program_period,
        raw_body=raw_project,
        payload_uri=raw_ref.payload_uri,
        content_sha256=raw_ref.content_sha256,
        http_status=200,
        fetched_at=datetime.now(timezone.utc),
    )
    if ingest_result.project is not None:
        funding_project_id = ingest_result.project.funding_project_id
    else:
        funding_project_id = await _find_project_id(conn, mis_value=mis_code, program_period=program_period)
    if funding_project_id is None:
        return None

    return await _create_link(
        conn,
        act_id=act_id,
        funding_project_id=funding_project_id,
        link_method=link_method,
        confidence=confidence,
        evidence=evidence,
    )


def _period_overlaps(act_date: date | None, start: str | None, end: str | None, slack_days: int) -> bool:
    if act_date is None or (not start and not end):
        return False
    try:
        start_date = date.fromisoformat(start) if start else None
        end_date = date.fromisoformat(end) if end else None
    except ValueError:
        return False
    if start_date is not None and act_date < start_date - timedelta(days=slack_days):
        return False
    if end_date is not None and act_date > end_date + timedelta(days=slack_days):
        return False
    return True


async def _fetch_candidates(
    client: AnaptyxiClient, *, beneficiary_afm: str | None, contractor_afm: str | None
) -> AnaptyxiBeneficiarySearchResponse | None:
    for afm in (beneficiary_afm, contractor_afm):
        if not afm:
            continue
        try:
            response = await client.find_projects_by_beneficiary_afm(afm)
        except ProjectNotFoundError:
            continue
        if response.results:
            return response
    return None


def _try_level2_afm_title_period(
    candidates: list[dict[str, Any]], *, act_title: str | None, act_date: date | None
) -> tuple[dict[str, Any], float] | None:
    scored = []
    for raw_project in candidates:
        title = raw_project.get("title") or raw_project.get("projectTitle")
        title_score = normalized_similarity(title, act_title)
        if title_score < LEVEL2_TITLE_SIMILARITY_THRESHOLD:
            continue
        if not _period_overlaps(
            act_date, raw_project.get("startDate"), raw_project.get("endDate"), LEVEL2_PERIOD_SLACK_DAYS
        ):
            continue
        scored.append((raw_project, title_score))
    return scored[0] if len(scored) == 1 else None


def _try_level3_ada_adam_in_metadata(
    candidates: list[dict[str, Any]], *, related_ada_candidates: list[str]
) -> tuple[dict[str, Any], str] | None:
    if not related_ada_candidates:
        return None
    matches = []
    for raw_project in candidates:
        haystack = json.dumps(raw_project, ensure_ascii=False).upper()
        for ada in related_ada_candidates:
            if ada and ada.strip().upper() in haystack:
                matches.append((raw_project, ada))
                break
    return matches[0] if len(matches) == 1 else None


def _try_level4_fuzzy(
    candidates: list[dict[str, Any]],
    *,
    act_title: str | None,
    act_amount: Decimal | None,
    act_region: str | None,
) -> tuple[dict[str, Any], float] | None:
    scored = []
    for raw_project in candidates:
        title = raw_project.get("title") or raw_project.get("projectTitle")
        title_score = normalized_similarity(title, act_title)
        if title_score < LEVEL4_TITLE_SIMILARITY_THRESHOLD:
            continue

        if act_amount is not None:
            project_amount = raw_project.get("budget") or raw_project.get("totalPublicExpenditure")
            if project_amount is None:
                continue
            try:
                project_amount = Decimal(str(project_amount))
            except Exception:
                continue
            tolerance = act_amount * Decimal(str(LEVEL4_AMOUNT_TOLERANCE))
            if abs(project_amount - act_amount) > tolerance:
                continue

        project_region = raw_project.get("nutsCode") or raw_project.get("region")
        if act_region and project_region and act_region.strip().upper() != str(project_region).strip().upper():
            continue  # both sides carry a region and they disagree — reject

        scored.append((raw_project, title_score))
    return scored[0] if len(scored) == 1 else None


async def resolve_funding_link_for_act(
    conn: AsyncConnection,
    *,
    client: AnaptyxiClient,
    raw_store: RawStore,
    act_id: uuid.UUID,
    mis_candidates: list[tuple[str, str]],  # [(field_name, value), ...] — e.g. [("publicFundingRefOps", "..."), ...]
    beneficiary_afm: str | None = None,
    contractor_afm: str | None = None,
    act_title: str | None = None,
    act_date: date | None = None,
    related_ada_candidates: list[str] | None = None,
    act_amount: Decimal | None = None,
    act_region: str | None = None,
) -> uuid.UUID | None:
    """Tries each join-hierarchy level in order (§19.2), stopping at the
    first that produces exactly one unambiguous match. Returns the
    `funding_project_id`, or None if nothing resolves at any level — not an
    error, just an unlinked act."""
    program_period = client.program_period

    # Level 1: exact ΟΠΣ/MIS code
    for field_name, mis_value in mis_candidates:
        if not mis_value:
            continue
        try:
            response = await client.find_project_by_mis(mis_value)
        except ProjectNotFoundError:
            continue

        raw_ref = await raw_store.put(
            source="anaptyxi",
            resource=program_period,
            partition_key=f"mis={mis_value}",
            payload=response.raw_body or json.dumps(response.body).encode("utf-8"),
        )
        ingest_result = await ingest_project_record(
            conn,
            mis_code=mis_value,
            program_period=program_period,
            raw_body=response.body,
            payload_uri=raw_ref.payload_uri,
            content_sha256=raw_ref.content_sha256,
            http_status=response.http_status,
            fetched_at=datetime.now(timezone.utc),
        )
        if ingest_result.project is not None:
            funding_project_id = ingest_result.project.funding_project_id
        else:
            funding_project_id = await _find_project_id(conn, mis_value=mis_value, program_period=program_period)
        if funding_project_id is None:
            continue

        return await _create_link(
            conn,
            act_id=act_id,
            funding_project_id=funding_project_id,
            link_method="MIS_OPS_EXACT",
            confidence=FUNDING_REF_EXACT_MIS_CONFIDENCE,
            evidence={"matched_field": field_name, "mis_value": mis_value},
        )

    # Levels 2-4 share one beneficiary/contractor-ΑΦΜ-scoped candidate set
    if not beneficiary_afm and not contractor_afm:
        return None
    search_response = await _fetch_candidates(client, beneficiary_afm=beneficiary_afm, contractor_afm=contractor_afm)
    if search_response is None:
        return None
    candidates = search_response.results

    level2_match = _try_level2_afm_title_period(candidates, act_title=act_title, act_date=act_date)
    if level2_match is not None:
        raw_project, title_score = level2_match
        return await _ingest_candidate_and_link(
            conn,
            raw_store=raw_store,
            program_period=program_period,
            act_id=act_id,
            raw_project=raw_project,
            link_method="AFM_TITLE_PERIOD",
            confidence=LEVEL2_CONFIDENCE,
            evidence={"matched_afm": search_response.afm, "title_similarity": round(title_score, 4)},
        )

    level3_match = _try_level3_ada_adam_in_metadata(candidates, related_ada_candidates=related_ada_candidates or [])
    if level3_match is not None:
        raw_project, matched_ada = level3_match
        return await _ingest_candidate_and_link(
            conn,
            raw_store=raw_store,
            program_period=program_period,
            act_id=act_id,
            raw_project=raw_project,
            link_method="ADA_ADAM_IN_METADATA",
            confidence=LEVEL3_CONFIDENCE,
            evidence={"matched_afm": search_response.afm, "matched_ada": matched_ada},
        )

    level4_match = _try_level4_fuzzy(candidates, act_title=act_title, act_amount=act_amount, act_region=act_region)
    if level4_match is not None:
        raw_project, title_score = level4_match
        return await _ingest_candidate_and_link(
            conn,
            raw_store=raw_store,
            program_period=program_period,
            act_id=act_id,
            raw_project=raw_project,
            link_method="FUZZY_TITLE_AMOUNT_REGION",
            confidence=LEVEL4_CONFIDENCE,
            evidence={
                "matched_afm": search_response.afm,
                "title_similarity": round(title_score, 4),
                "needs_review": True,
            },
        )

    return None

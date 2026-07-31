"""Materialize legally sourced public-sector stakeholder intelligence."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

_ROLE_TERMS = (
    ("PROCUREMENT", ("προμηθει", "συμβασ", "διαγωνισ", "procurement", "contracting")),
    ("FINANCE", ("οικονομ", "δημοσιονομ", "λογιστηρ", "finance", "accounting")),
    ("TECHNICAL", ("τεχνικ", "μηχανικ", "πληροφορικ", "technical", "engineer", "digital")),
    ("DEPARTMENT_HEAD", ("διευθυν", "προϊσταμ", "head of", "director", "manager")),
    ("SIGNATORY", ("υπογραφ", "δήμαρχ", "περιφερειάρχ", "γενικ γραμματ", "signatory")),
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[+()\d\s./-]{7,30}$")


@dataclass(frozen=True)
class PublicContact:
    email: str | None
    phone: str | None
    profile_url: str | None


def classify_decision_role(job_title: str | None, department: str | None) -> str:
    """Classify a published title without inferring authority beyond its wording."""
    text = " ".join(part.casefold() for part in (job_title, department) if part)
    for role, terms in _ROLE_TERMS:
        if any(term in text for term in terms):
            return role
    return "STAKEHOLDER"


def public_contact_fields(raw: Mapping[str, Any] | None) -> PublicContact:
    """Return only syntactically valid contact fields present in an official record."""
    payload = raw or {}
    email = next(
        (
            str(payload[key]).strip()
            for key in ("email", "emailAddress", "contactEmail")
            if payload.get(key) and _EMAIL_RE.fullmatch(str(payload[key]).strip())
        ),
        None,
    )
    phone = next(
        (
            str(payload[key]).strip()
            for key in ("phone", "telephone", "telephoneNumber", "contactPhone")
            if payload.get(key) and _PHONE_RE.fullmatch(str(payload[key]).strip())
        ),
        None,
    )
    profile_url = next(
        (
            str(payload[key]).strip()
            for key in ("profileUrl", "profileURL", "url")
            if str(payload.get(key) or "").startswith(("https://", "http://"))
        ),
        None,
    )
    return PublicContact(email=email, phone=phone, profile_url=profile_url)


def _first_text(raw: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


async def _buyer_for_organization(
    conn: AsyncConnection,
    *,
    vat_number: str | None,
    label: str,
) -> uuid.UUID | None:
    from packages.domain.tables import entities, entity_identifiers

    if vat_number:
        normalized_vat = "".join(character for character in vat_number if character.isdigit())
        if normalized_vat:
            resolved = (
                await conn.execute(
                    sa.select(entity_identifiers.c.entity_id)
                    .where(
                        entity_identifiers.c.scheme.in_(("AFM", "VAT")),
                        entity_identifiers.c.value_normalized == normalized_vat,
                        entity_identifiers.c.identifier_valid.is_(True),
                    )
                    .order_by(entity_identifiers.c.confidence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if resolved is not None:
                return resolved

    return (
        await conn.execute(
            sa.select(entities.c.id)
            .where(
                entities.c.entity_type.in_(("ORGANIZATION", "PUBLIC_BODY")),
                entities.c.normalized_name == label.strip().upper(),
                entities.c.merged_into_id.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def refresh_decision_makers(conn: AsyncConnection) -> dict[str, int]:
    """Upsert stakeholders from already-ingested Διαύγεια and ΑΝΑΠΤΥΞΗ records."""
    from packages.domain.tables import (
        decision_makers,
        diavgeia_organizations,
        diavgeia_signers,
        funding_project_bodies,
    )

    counts = {"diavgeia": 0, "anaptyxi": 0, "unresolved_buyers": 0}
    signer_rows = (
        await conn.execute(
            sa.select(
                diavgeia_signers,
                diavgeia_organizations.c.label.label("organization_label"),
                diavgeia_organizations.c.vat_number,
            ).select_from(
                diavgeia_signers.join(
                    diavgeia_organizations,
                    diavgeia_organizations.c.uid == diavgeia_signers.c.organization_uid,
                )
            )
        )
    ).all()

    for row in signer_rows:
        buyer_id = await _buyer_for_organization(
            conn,
            vat_number=row.vat_number,
            label=row.organization_label,
        )
        if buyer_id is None:
            counts["unresolved_buyers"] += 1
            continue
        full_name = " ".join(
            part.strip() for part in (row.first_name or "", row.last_name or "") if part.strip()
        )
        if not full_name:
            continue
        raw = row.raw if isinstance(row.raw, dict) else {}
        job_title = _first_text(raw, "jobTitle", "position", "title")
        department = _first_text(raw, "department", "unitLabel", "organizationUnit")
        contact = public_contact_fields(raw)
        statement = pg_insert(decision_makers).values(
            id=uuid.uuid4(),
            buyer_entity_id=buyer_id,
            full_name=full_name,
            job_title=job_title,
            department=department,
            decision_role=classify_decision_role(job_title, department),
            email=contact.email,
            phone=contact.phone,
            profile_url=contact.profile_url,
            source_system="DIAVGEIA",
            source_url=f"https://diavgeia.gov.gr/opendata/signers/{row.uid}",
            source_identifier=row.uid,
            source_record_id=row.source_record_id,
            legal_basis="PUBLIC_OFFICIAL_RECORD",
            confidence=0.95,
            active_from=row.active_from,
            active_until=row.active_until,
            is_current=row.active is not False,
            evidence={"organization_uid": row.organization_uid, "organization": row.organization_label},
            observed_at=row.observed_at,
        )
        await conn.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    decision_makers.c.buyer_entity_id,
                    decision_makers.c.full_name,
                    decision_makers.c.decision_role,
                    decision_makers.c.source_system,
                    decision_makers.c.source_identifier,
                ],
                set_={
                    "job_title": statement.excluded.job_title,
                    "department": statement.excluded.department,
                    "email": statement.excluded.email,
                    "phone": statement.excluded.phone,
                    "profile_url": statement.excluded.profile_url,
                    "source_record_id": statement.excluded.source_record_id,
                    "active_from": statement.excluded.active_from,
                    "active_until": statement.excluded.active_until,
                    "is_current": statement.excluded.is_current,
                    "evidence": statement.excluded.evidence,
                    "observed_at": statement.excluded.observed_at,
                },
            )
        )
        counts["diavgeia"] += 1

    body_rows = (
        await conn.execute(
            sa.select(funding_project_bodies).where(
                funding_project_bodies.c.entity_id.is_not(None),
                funding_project_bodies.c.representative.is_not(None),
            )
        )
    ).all()
    for row in body_rows:
        full_name = str(row.representative or "").strip()
        if not full_name:
            continue
        source_identifier = str(row.id)
        statement = pg_insert(decision_makers).values(
            id=uuid.uuid4(),
            buyer_entity_id=row.entity_id,
            full_name=full_name,
            job_title=row.body_category,
            department=row.name,
            decision_role=classify_decision_role(row.body_category, row.name),
            email=row.email if row.email and _EMAIL_RE.fullmatch(row.email.strip()) else None,
            phone=row.telephone if row.telephone and _PHONE_RE.fullmatch(row.telephone.strip()) else None,
            source_system="ANAPTYXI",
            source_identifier=source_identifier,
            source_record_id=row.source_record_id,
            legal_basis="PUBLIC_OFFICIAL_RECORD",
            confidence=0.9,
            is_current=True,
            evidence={"project_id": str(row.funding_project_id), "body_category": row.body_category},
            observed_at=row.observed_at,
        )
        await conn.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    decision_makers.c.buyer_entity_id,
                    decision_makers.c.full_name,
                    decision_makers.c.decision_role,
                    decision_makers.c.source_system,
                    decision_makers.c.source_identifier,
                ],
                set_={
                    "job_title": statement.excluded.job_title,
                    "department": statement.excluded.department,
                    "email": statement.excluded.email,
                    "phone": statement.excluded.phone,
                    "source_record_id": statement.excluded.source_record_id,
                    "evidence": statement.excluded.evidence,
                    "observed_at": statement.excluded.observed_at,
                },
            )
        )
        counts["anaptyxi"] += 1
    return counts

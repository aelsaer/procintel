"""Field-level provenance and metric methodology for the evidence drawer."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import field_provenance, procurement_acts, source_records

from ..db import get_conn

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])


class FieldEvidenceResponse(BaseModel):
    field_name: str
    source: str
    source_native_id: str | None
    source_path: str | None
    extraction_method: str
    confidence: float
    observed_at: datetime
    retrieved_at: datetime
    license_code: str | None
    source_record_id: str


class EvidenceResponse(BaseModel):
    object_type: str
    object_id: str
    freshness: datetime | None
    minimum_confidence: float | None
    fields: list[FieldEvidenceResponse]


class MetricMethodologyResponse(BaseModel):
    metric: str
    label: str
    formula: str
    value_basis: str
    minimum_sample: int
    limitations: list[str]
    source_tables: list[str]


_METHODOLOGIES: dict[str, MetricMethodologyResponse] = {
    "hhi": MetricMethodologyResponse(metric="hhi", label="HHI συγκέντρωσης", formula="Σ supplier_share_i²", value_basis="current_contract_value_net", minimum_sample=2, limitations=["Οι μη ταυτοποιημένοι ανάδοχοι μειώνουν την κάλυψη.", "Δεν αποτελεί ένδειξη εύνοιας."], source_tables=["procurement_acts", "act_parties", "market_hhi"]),
    "buyer_concentration": MetricMethodologyResponse(metric="buyer_concentration", label="Συγκέντρωση αγοραστή", formula="Αξία κορυφαίου προμηθευτή / συνολική καταγεγραμμένη αξία αγοραστή", value_basis="recorded_contract_value_net", minimum_sample=1, limitations=["Αφορά μόνο καταγεγραμμένες δημόσιες συμβάσεις."], source_tables=["procurement_acts", "act_parties", "buyer_concentration"]),
    "supplier_dependency": MetricMethodologyResponse(metric="supplier_dependency", label="Εξάρτηση προμηθευτή", formula="Αξία από αγοραστή / συνολική καταγεγραμμένη δημόσια αξία προμηθευτή", value_basis="recorded_contract_value_net", minimum_sample=1, limitations=["Δεν περιλαμβάνει ιδιωτικά έσοδα."], source_tables=["procurement_acts", "act_parties", "supplier_dependency"]),
    "incumbency": MetricMethodologyResponse(metric="incumbency", label="Likely incumbent", formula="Πιο πρόσφατη ενεργή σύμβαση ίδιου αγοραστή και CPV4", value_basis="latest_active_contract", minimum_sample=1, limitations=["Είναι εξηγήσιμο heuristic, όχι πρόβλεψη νικητή."], source_tables=["procurement_acts", "act_links", "incumbent_signals"]),
    "modification": MetricMethodologyResponse(metric="modification", label="Τροποποιήσεις και value uplift", formula="(τρέχουσα - αρχική αξία) / αρχική αξία", value_basis="confirmed_amendment_links", minimum_sample=1, limitations=["Υπολογίζονται μόνο συνδέσεις AMENDS."], source_tables=["act_links", "contract_modification_stats"]),
    "cycle_time": MetricMethodologyResponse(metric="cycle_time", label="Χρόνος διαδικασίας", formula="Διαφορά ημερομηνιών μεταξύ lifecycle κόμβων", value_basis="high_confidence_links", minimum_sample=1, limitations=["Χρησιμοποιούνται lifecycle links confidence ≥ 0.95."], source_tables=["act_links", "cycle_time_metrics"]),
    "payment_execution": MetricMethodologyResponse(metric="payment_execution", label="Εκτέλεση πληρωμών", formula="Συνδεδεμένο ποσό πληρωμών / τρέχουσα αξία σύμβασης", value_basis="linked_payment_amount", minimum_sample=1, limitations=["Η κάλυψη διαφέρει ανά πηγή και δεν ισοδυναμεί πάντα με ταμειακή πληρωμή."], source_tables=["procurement_acts", "act_links", "payment_execution"]),
    "renewal": MetricMethodologyResponse(metric="renewal", label="Renewal watch", formula="ημέρες έως λήξη ≤ μέσο procurement lead time αγοραστή", value_basis="contract_end_date", minimum_sample=1, limitations=["Rule-based σήμα, όχι βεβαιότητα νέου διαγωνισμού."], source_tables=["renewal_signals", "cycle_time_metrics"]),
    "opportunity_score": MetricMethodologyResponse(metric="opportunity_score", label="Opportunity score", formula="25% CPV fit + 20% buyer affinity + 15% timing + 15% competition + 15% value fit + 10% confidence", value_basis="tenant_business_profile", minimum_sample=1, limitations=["Δεν είναι win probability."], source_tables=["business_profiles", "opportunity_scores"]),
}


@router.get("/methodologies", response_model=list[MetricMethodologyResponse])
async def list_methodologies() -> list[MetricMethodologyResponse]:
    return list(_METHODOLOGIES.values())


@router.get("/methodologies/{metric}", response_model=MetricMethodologyResponse)
async def get_methodology(metric: str) -> MetricMethodologyResponse:
    if metric not in _METHODOLOGIES:
        raise HTTPException(status_code=404, detail="Metric methodology not found")
    return _METHODOLOGIES[metric]


@router.get("/{object_type}/{object_id}", response_model=EvidenceResponse)
async def get_object_evidence(
    object_type: str, object_id: str,
    conn: AsyncConnection = Depends(get_conn),
) -> EvidenceResponse:
    try:
        target_id = uuid.UUID(object_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="object_id is not a valid UUID") from exc
    rows = (await conn.execute(
        sa.select(
            field_provenance,
            source_records.c.source_system,
            source_records.c.source_native_id,
            source_records.c.fetched_at,
            source_records.c.license_code,
        )
        .join(source_records, source_records.c.id == field_provenance.c.source_record_id)
        .where(field_provenance.c.object_type == object_type, field_provenance.c.object_id == target_id)
        .order_by(field_provenance.c.field_name, field_provenance.c.observed_at.desc())
    )).all()
    fields = [FieldEvidenceResponse(
        field_name=row.field_name, source=row.source_system,
        source_native_id=row.source_native_id, source_path=row.source_path,
        extraction_method=row.extraction_method, confidence=float(row.confidence),
        observed_at=row.observed_at, retrieved_at=row.fetched_at,
        license_code=row.license_code, source_record_id=str(row.source_record_id),
    ) for row in rows]

    # Older loaded records predate field_provenance writes. Their canonical
    # rows still retain an exact source_record_id, so expose that lineage now
    # instead of presenting an empty drawer until the next re-ingestion.
    if object_type == "procurement_processes":
        act_rows = (await conn.execute(
            sa.select(
                procurement_acts,
                source_records.c.source_system,
                source_records.c.source_native_id,
                source_records.c.fetched_at,
                source_records.c.license_code,
            ).join(source_records, source_records.c.id == procurement_acts.c.source_record_id)
            .where(procurement_acts.c.process_id == target_id, procurement_acts.c.is_current.is_(True))
            .order_by(source_records.c.fetched_at.desc()).limit(50)
        )).all()
        paths = {
            "title": "$.title", "submission_date": "$.submissionDate",
            "publication_date": "$.publicationDate", "decision_date": "$.decisionDate",
            "end_date": "$.contractEndDate|$.endDate", "amount_net": "$.totalCostWithoutVAT",
            "amount_gross": "$.totalCostWithVAT", "status": "$.status",
            "procedure_type": "$.procedureType", "act_type": "$resource",
        }
        existing = {(field.field_name, field.source_record_id) for field in fields}
        for act in act_rows:
            for field_name, path in paths.items():
                if getattr(act, field_name) is None:
                    continue
                key = (f"{act.act_type.lower()}.{field_name}", str(act.source_record_id))
                if key in existing:
                    continue
                fields.append(FieldEvidenceResponse(
                    field_name=key[0], source=act.source_system,
                    source_native_id=act.source_native_id, source_path=path,
                    extraction_method="DIRECT_FIELD_MAPPING",
                    confidence=1.0, observed_at=act.fetched_at,
                    retrieved_at=act.fetched_at, license_code=act.license_code,
                    source_record_id=str(act.source_record_id),
                ))
                existing.add(key)
    elif object_type == "entities":
        entity_rows = (await conn.execute(sa.text(
            """
            SELECT 'name' AS field_name, sr.source_system, sr.source_native_id,
                   sr.fetched_at, sr.license_code, sr.id AS source_record_id,
                   '$.organizationName|$.name' AS source_path,
                   0.95::numeric AS confidence
            FROM entity_names n JOIN source_records sr ON sr.id=n.source_record_id
            WHERE n.entity_id=CAST(:id AS uuid)
            UNION ALL
            SELECT LOWER(i.scheme), sr.source_system, sr.source_native_id,
                   sr.fetched_at, sr.license_code, sr.id,
                   '$.organizationVatNumber|$.vatNumber|$.afm', i.confidence
            FROM entity_identifiers i JOIN source_records sr ON sr.id=i.source_record_id
            WHERE i.entity_id=CAST(:id AS uuid)
            ORDER BY fetched_at DESC LIMIT 50
            """
        ), {"id": str(target_id)})).mappings().all()
        for row in entity_rows:
            fields.append(FieldEvidenceResponse(
                field_name=row["field_name"], source=row["source_system"],
                source_native_id=row["source_native_id"], source_path=row["source_path"],
                extraction_method="DIRECT_FIELD_MAPPING", confidence=float(row["confidence"]),
                observed_at=row["fetched_at"], retrieved_at=row["fetched_at"],
                license_code=row["license_code"], source_record_id=str(row["source_record_id"]),
            ))
    return EvidenceResponse(
        object_type=object_type, object_id=object_id,
        freshness=max((field.retrieved_at for field in fields), default=None),
        minimum_confidence=min((field.confidence for field in fields), default=None),
        fields=fields,
    )

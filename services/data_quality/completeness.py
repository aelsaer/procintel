"""Source-level completeness scoring with explicit evidence boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class SourceCompletenessInput:
    source_system: str
    observed_records: int
    parsed_records: int
    canonical_records: int
    applicable_document_records: int
    records_with_documents: int
    applicable_party_records: int
    records_with_parties: int
    applicable_location_records: int
    records_with_locations: int
    failed_records: int
    pending_enrichments: int
    freshness_seconds: int | None
    freshness_target_seconds: int
    minimum_completeness: float
    expected_records: int | None = None
    expected_basis: str = "OBSERVED_ONLY"


@dataclass(frozen=True)
class SourceCompletenessAssessment:
    source_system: str
    status: str
    score: float
    claim_level: str
    expected_basis: str
    observed_records: int
    expected_records: int | None
    canonical_records: int
    records_with_documents: int
    records_with_parties: int
    records_with_locations: int
    failed_records: int
    pending_enrichments: int
    freshness_seconds: int | None
    freshness_target_seconds: int
    dimensions: dict[str, float | None]
    findings: tuple[dict[str, Any], ...]


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(max(0.0, min(100.0, numerator * 100.0 / denominator)), 3)


def _weighted_rate(
    dimensions: list[tuple[float | None, float]],
) -> float:
    available = [(value, weight) for value, weight in dimensions if value is not None]
    if not available:
        return 0.0
    weight_total = sum(weight for _, weight in available)
    return sum(float(value) * weight for value, weight in available) / weight_total


def assess_source(metrics: SourceCompletenessInput) -> SourceCompletenessAssessment:
    expected = metrics.expected_records if metrics.expected_records and metrics.expected_records > 0 else None
    ingestion_denominator = expected or metrics.observed_records
    ingest_rate = _rate(
        min(metrics.parsed_records, ingestion_denominator),
        ingestion_denominator,
    )
    canonical_rate = _rate(metrics.canonical_records, metrics.parsed_records)
    document_rate = _rate(
        metrics.records_with_documents,
        metrics.applicable_document_records,
    )
    party_rate = _rate(metrics.records_with_parties, metrics.applicable_party_records)
    location_rate = _rate(
        metrics.records_with_locations,
        metrics.applicable_location_records,
    )
    if metrics.freshness_seconds is None:
        freshness_rate = 0.0
    elif metrics.freshness_seconds <= metrics.freshness_target_seconds:
        freshness_rate = 100.0
    else:
        freshness_rate = max(
            0.0,
            100.0
            - (
                (metrics.freshness_seconds - metrics.freshness_target_seconds)
                / metrics.freshness_target_seconds
            )
            * 100.0,
        )

    score = _weighted_rate(
        [
            (ingest_rate, 35),
            (canonical_rate, 25),
            (document_rate, 15),
            (party_rate, 15),
            (location_rate, 5),
            (freshness_rate, 10),
        ]
    )
    queue_penalty = min(15.0, metrics.pending_enrichments / max(metrics.observed_records, 1) * 100)
    failure_penalty = min(20.0, metrics.failed_records / max(metrics.observed_records, 1) * 100)
    score = round(max(0.0, min(100.0, score - queue_penalty - failure_penalty)), 2)

    findings: list[dict[str, Any]] = []
    if expected is None:
        findings.append(
            {
                "code": "UPSTREAM_TOTAL_NOT_VERIFIED",
                "severity": "INFO",
                "message": "Η πηγή δεν δημοσιεύει επαληθεύσιμο συνολικό πλήθος για το παράθυρο.",
            }
        )
    if metrics.freshness_seconds is None:
        findings.append(
            {
                "code": "NO_SUCCESSFUL_FETCH",
                "severity": "ERROR",
                "message": "Δεν υπάρχει επιτυχής ανάκτηση για την πηγή.",
            }
        )
    elif metrics.freshness_seconds > metrics.freshness_target_seconds:
        findings.append(
            {
                "code": "FRESHNESS_SLO_MISSED",
                "severity": "WARNING",
                "message": "Η τελευταία ανάκτηση είναι παλαιότερη από τον στόχο.",
            }
        )
    for name, value, threshold in (
        ("CANONICAL_LINK_RATE", canonical_rate, 95),
        ("DOCUMENT_RATE", document_rate, 80),
        ("PARTY_RATE", party_rate, 85),
    ):
        if value is not None and value < threshold:
            findings.append(
                {
                    "code": name,
                    "severity": "WARNING",
                    "message": f"Η κάλυψη είναι {value:.1f}% έναντι στόχου {threshold}%.",
                }
            )
    if metrics.pending_enrichments:
        findings.append(
            {
                "code": "PENDING_ENRICHMENTS",
                "severity": "INFO",
                "message": f"{metrics.pending_enrichments} enrichments εκκρεμούν.",
            }
        )

    stale = (
        metrics.freshness_seconds is None
        or metrics.freshness_seconds > metrics.freshness_target_seconds * 2
    )
    if metrics.observed_records <= 0:
        status = "UNAVAILABLE"
    elif stale:
        status = "STALE"
    elif score >= metrics.minimum_completeness:
        status = "HEALTHY"
    elif score >= 70:
        status = "DEGRADED"
    else:
        status = "PARTIAL"

    return SourceCompletenessAssessment(
        source_system=metrics.source_system,
        status=status,
        score=score,
        claim_level="VERIFIED_WINDOW" if expected is not None else "OBSERVED_COVERAGE",
        expected_basis=metrics.expected_basis,
        observed_records=metrics.observed_records,
        expected_records=expected,
        canonical_records=metrics.canonical_records,
        records_with_documents=metrics.records_with_documents,
        records_with_parties=metrics.records_with_parties,
        records_with_locations=metrics.records_with_locations,
        failed_records=metrics.failed_records,
        pending_enrichments=metrics.pending_enrichments,
        freshness_seconds=metrics.freshness_seconds,
        freshness_target_seconds=metrics.freshness_target_seconds,
        dimensions={
            "ingestion": ingest_rate,
            "canonical": canonical_rate,
            "documents": document_rate,
            "parties": party_rate,
            "locations": location_rate,
            "freshness": round(freshness_rate, 3),
        },
        findings=tuple(findings),
    )


async def collect_source_completeness(
    conn: AsyncConnection,
) -> list[SourceCompletenessAssessment]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH source_stats AS (
                    SELECT
                        source.source_system,
                        COUNT(*) AS observed_records,
                        COUNT(*) FILTER (WHERE source.parse_status = 'PARSED') AS parsed_records,
                        COUNT(*) FILTER (WHERE source.parse_status = 'FAILED') AS failed_records,
                        MAX(source.fetched_at) AS latest_fetched_at
                    FROM source_records source
                    WHERE source.source_system IN (
                        'KHMDHS', 'DIAVGEIA', 'GEMI', 'ANAPTYXI', 'MEF', 'TED'
                    )
                    GROUP BY source.source_system
                ),
                document_acts AS MATERIALIZED (
                    SELECT DISTINCT document.act_id
                    FROM documents document
                    WHERE document.act_id IS NOT NULL
                ),
                party_acts AS MATERIALIZED (
                    SELECT DISTINCT party.act_id
                    FROM act_parties party
                ),
                located_acts AS MATERIALIZED (
                    SELECT DISTINCT location.act_id
                    FROM act_locations location
                ),
                act_stats AS (
                    SELECT
                        source.source_system,
                        COUNT(*) AS canonical_records,
                        COUNT(*) FILTER (
                            WHERE act.act_type IN ('NOTICE', 'AWARD', 'CONTRACT', 'TED_NOTICE')
                        ) AS applicable_document_records,
                        COUNT(*) FILTER (
                            WHERE document_acts.act_id IS NOT NULL
                        ) AS records_with_documents,
                        COUNT(*) FILTER (
                            WHERE act.act_type IN ('REQUEST', 'NOTICE', 'AWARD', 'CONTRACT', 'TED_NOTICE')
                        ) AS applicable_party_records,
                        COUNT(*) FILTER (
                            WHERE party_acts.act_id IS NOT NULL
                        ) AS records_with_parties,
                        COUNT(*) FILTER (
                            WHERE act.act_type IN ('REQUEST', 'NOTICE', 'CONTRACT', 'TED_NOTICE')
                        ) AS applicable_location_records,
                        COUNT(*) FILTER (
                            WHERE located_acts.act_id IS NOT NULL
                        ) AS records_with_locations
                    FROM procurement_acts act
                    JOIN source_records source ON source.id = act.source_record_id
                    LEFT JOIN document_acts ON document_acts.act_id = act.id
                    LEFT JOIN party_acts ON party_acts.act_id = act.id
                    LEFT JOIN located_acts ON located_acts.act_id = act.id
                    WHERE act.is_current = TRUE
                    GROUP BY source.source_system
                ),
                latest_runs AS (
                    SELECT DISTINCT ON (run.source_system)
                        run.source_system,
                        NULLIF(
                            COALESCE(
                                run.metrics->>'upstream_total',
                                run.metrics->>'expected_records'
                            ),
                            ''
                        )::INTEGER AS expected_records,
                        CASE
                            WHEN COALESCE(
                                run.metrics->>'upstream_total',
                                run.metrics->>'expected_records'
                            ) IS NULL THEN 'OBSERVED_ONLY'
                            ELSE 'UPSTREAM_TOTAL'
                        END AS expected_basis
                    FROM connector_runs run
                    WHERE run.status IN ('SUCCEEDED', 'PARTIAL')
                    ORDER BY run.source_system, run.started_at DESC
                ),
                pending AS (
                    SELECT
                        CASE
                            WHEN provider LIKE 'KHMDHS%' THEN 'KHMDHS'
                            WHEN provider LIKE 'DIAVGEIA%' THEN 'DIAVGEIA'
                            WHEN provider LIKE 'GEMI%' THEN 'GEMI'
                            WHEN provider LIKE 'ANAPTYXI%' THEN 'ANAPTYXI'
                            WHEN provider LIKE 'MEF%' THEN 'MEF'
                            WHEN provider LIKE 'TED%' OR provider LIKE 'VIES%' THEN 'TED'
                        END AS source_system,
                        COUNT(*) AS pending_enrichments
                    FROM enrichment_jobs
                    WHERE status IN ('QUEUED', 'RUNNING', 'FAILED', 'BLOCKED_CONFIG')
                    GROUP BY 1
                )
                SELECT
                    target.source_system,
                    target.freshness_target_seconds,
                    target.minimum_completeness,
                    COALESCE(source.observed_records, 0) AS observed_records,
                    COALESCE(source.parsed_records, 0) AS parsed_records,
                    COALESCE(source.failed_records, 0) AS failed_records,
                    EXTRACT(EPOCH FROM (now() - source.latest_fetched_at))::BIGINT
                        AS freshness_seconds,
                    COALESCE(acts.canonical_records, 0) AS canonical_records,
                    COALESCE(acts.applicable_document_records, 0)
                        AS applicable_document_records,
                    COALESCE(acts.records_with_documents, 0) AS records_with_documents,
                    COALESCE(acts.applicable_party_records, 0) AS applicable_party_records,
                    COALESCE(acts.records_with_parties, 0) AS records_with_parties,
                    COALESCE(acts.applicable_location_records, 0)
                        AS applicable_location_records,
                    COALESCE(acts.records_with_locations, 0) AS records_with_locations,
                    COALESCE(pending.pending_enrichments, 0) AS pending_enrichments,
                    latest_runs.expected_records,
                    COALESCE(latest_runs.expected_basis, 'OBSERVED_ONLY') AS expected_basis
                FROM source_service_levels target
                LEFT JOIN source_stats source USING (source_system)
                LEFT JOIN act_stats acts USING (source_system)
                LEFT JOIN pending USING (source_system)
                LEFT JOIN latest_runs USING (source_system)
                ORDER BY target.source_system
                """
            )
        )
    ).mappings().all()
    return [
        assess_source(
            SourceCompletenessInput(
                source_system=row["source_system"],
                observed_records=int(row["observed_records"]),
                parsed_records=int(row["parsed_records"]),
                canonical_records=int(row["canonical_records"]),
                applicable_document_records=int(row["applicable_document_records"]),
                records_with_documents=int(row["records_with_documents"]),
                applicable_party_records=int(row["applicable_party_records"]),
                records_with_parties=int(row["records_with_parties"]),
                applicable_location_records=int(row["applicable_location_records"]),
                records_with_locations=int(row["records_with_locations"]),
                failed_records=int(row["failed_records"]),
                pending_enrichments=int(row["pending_enrichments"]),
                freshness_seconds=(
                    int(row["freshness_seconds"])
                    if row["freshness_seconds"] is not None
                    else None
                ),
                freshness_target_seconds=int(row["freshness_target_seconds"]),
                minimum_completeness=float(row["minimum_completeness"]),
                expected_records=(
                    int(row["expected_records"])
                    if row["expected_records"] is not None
                    else None
                ),
                expected_basis=row["expected_basis"],
            )
        )
        for row in rows
    ]


async def persist_source_completeness_snapshots(
    conn: AsyncConnection,
    *,
    snapshot_date: date | None = None,
) -> list[SourceCompletenessAssessment]:
    assessments = await collect_source_completeness(conn)
    effective_date = snapshot_date or datetime.now(timezone.utc).date()
    for assessment in assessments:
        await conn.execute(
            sa.text(
                """
                INSERT INTO source_completeness_snapshots (
                    source_system, snapshot_date, window_started_at, window_ended_at,
                    expected_records, observed_records, canonical_records,
                    records_with_documents, records_with_parties, records_with_locations,
                    failed_records, pending_enrichments, freshness_seconds,
                    completeness_score, status, dimensions, evidence
                )
                VALUES (
                    :source_system, :snapshot_date, :window_started_at, :window_ended_at,
                    :expected_records, :observed_records, :canonical_records,
                    :records_with_documents, :records_with_parties,
                    :records_with_locations, :failed_records, :pending_enrichments,
                    :freshness_seconds, :score, :status,
                    CAST(:dimensions AS JSONB), CAST(:evidence AS JSONB)
                )
                ON CONFLICT (
                    source_system, snapshot_date, window_started_at, window_ended_at
                ) DO UPDATE SET
                    expected_records = EXCLUDED.expected_records,
                    observed_records = EXCLUDED.observed_records,
                    canonical_records = EXCLUDED.canonical_records,
                    records_with_documents = EXCLUDED.records_with_documents,
                    records_with_parties = EXCLUDED.records_with_parties,
                    records_with_locations = EXCLUDED.records_with_locations,
                    failed_records = EXCLUDED.failed_records,
                    pending_enrichments = EXCLUDED.pending_enrichments,
                    freshness_seconds = EXCLUDED.freshness_seconds,
                    completeness_score = EXCLUDED.completeness_score,
                    status = EXCLUDED.status,
                    dimensions = EXCLUDED.dimensions,
                    evidence = EXCLUDED.evidence,
                    computed_at = now()
                """
            ),
            {
                "source_system": assessment.source_system,
                "snapshot_date": effective_date,
                "window_started_at": datetime.combine(
                    effective_date,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ),
                "window_ended_at": datetime.combine(
                    effective_date,
                    datetime.max.time(),
                    tzinfo=timezone.utc,
                ),
                "expected_records": assessment.expected_records,
                "observed_records": assessment.observed_records,
                "canonical_records": assessment.canonical_records,
                "records_with_documents": assessment.records_with_documents,
                "records_with_parties": assessment.records_with_parties,
                "records_with_locations": assessment.records_with_locations,
                "failed_records": assessment.failed_records,
                "pending_enrichments": assessment.pending_enrichments,
                "freshness_seconds": assessment.freshness_seconds,
                "score": assessment.score,
                "status": assessment.status,
                "dimensions": __import__("json").dumps(assessment.dimensions),
                "evidence": __import__("json").dumps(
                    {
                        "claim_level": assessment.claim_level,
                        "expected_basis": assessment.expected_basis,
                        "findings": list(assessment.findings),
                    }
                ),
            },
        )
    await conn.commit()
    return assessments

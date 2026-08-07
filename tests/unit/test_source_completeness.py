from unittest.mock import AsyncMock, Mock

import pytest

from services.data_quality.completeness import (
    SourceCompletenessInput,
    assess_source,
    collect_source_completeness,
)


def metrics(**overrides):
    values = {
        "source_system": "KHMDHS",
        "observed_records": 100,
        "parsed_records": 100,
        "canonical_records": 100,
        "applicable_document_records": 80,
        "records_with_documents": 80,
        "applicable_party_records": 90,
        "records_with_parties": 90,
        "applicable_location_records": 70,
        "records_with_locations": 65,
        "failed_records": 0,
        "pending_enrichments": 0,
        "freshness_seconds": 3600,
        "freshness_target_seconds": 108000,
        "minimum_completeness": 95,
        "expected_records": 100,
        "expected_basis": "UPSTREAM_TOTAL",
    }
    values.update(overrides)
    return SourceCompletenessInput(**values)


def test_complete_fresh_source_receives_verified_healthy_grade():
    assessment = assess_source(metrics())
    assert assessment.status == "HEALTHY"
    assert assessment.claim_level == "VERIFIED_WINDOW"
    assert assessment.score >= 95
    assert assessment.dimensions["documents"] == 100


def test_missing_upstream_total_is_disclosed_not_sold_as_verified():
    assessment = assess_source(
        metrics(expected_records=None, expected_basis="OBSERVED_ONLY")
    )
    assert assessment.claim_level == "OBSERVED_COVERAGE"
    assert any(
        item["code"] == "UPSTREAM_TOTAL_NOT_VERIFIED"
        for item in assessment.findings
    )


def test_stale_source_never_appears_healthy_even_with_complete_records():
    assessment = assess_source(metrics(freshness_seconds=300000))
    assert assessment.status == "STALE"
    assert assessment.dimensions["freshness"] == 0


def test_enrichment_backlog_and_missing_documents_reduce_score():
    assessment = assess_source(
        metrics(
            records_with_documents=20,
            pending_enrichments=30,
            expected_records=120,
        )
    )
    assert assessment.status in {"DEGRADED", "PARTIAL"}
    assert assessment.score < 80
    assert {item["code"] for item in assessment.findings} >= {
        "DOCUMENT_RATE",
        "PENDING_ENRICHMENTS",
    }


def test_empty_source_is_unavailable():
    assessment = assess_source(
        metrics(
            observed_records=0,
            parsed_records=0,
            canonical_records=0,
            expected_records=None,
            freshness_seconds=None,
        )
    )
    assert assessment.status == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_collection_preaggregates_related_acts_instead_of_correlated_scans():
    row = {
        "source_system": "KHMDHS",
        "freshness_target_seconds": 108000,
        "minimum_completeness": 95,
        "observed_records": 1,
        "parsed_records": 1,
        "failed_records": 0,
        "freshness_seconds": 60,
        "canonical_records": 1,
        "applicable_document_records": 1,
        "records_with_documents": 1,
        "applicable_party_records": 1,
        "records_with_parties": 1,
        "applicable_location_records": 1,
        "records_with_locations": 1,
        "pending_enrichments": 0,
        "expected_records": 1,
        "expected_basis": "UPSTREAM_TOTAL",
    }
    result = Mock()
    result.mappings.return_value.all.return_value = [row]
    conn = Mock(execute=AsyncMock(return_value=result))

    assessments = await collect_source_completeness(conn)

    query = str(conn.execute.await_args.args[0])
    assert "document_acts AS MATERIALIZED" in query
    assert "party_acts AS MATERIALIZED" in query
    assert "located_acts AS MATERIALIZED" in query
    assert "SELECT 1 FROM documents" not in query
    assert assessments[0].status == "HEALTHY"

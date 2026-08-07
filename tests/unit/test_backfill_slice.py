from datetime import date

from scripts.backfill_slice import (
    _build_parser,
    _build_verdict,
    _slice_opensearch_config,
)
from services.search_index.config import OpenSearchConfig


def test_slice_parser_supports_downstream_resume_without_changing_dates() -> None:
    args = _build_parser().parse_args(
        [
            "--database-url",
            "postgresql://procintel:procintel@localhost/procintel_slice_test",
            "--date-from",
            "2026-07-28",
            "--date-to",
            "2026-07-29",
            "--skip-primary-ingestion",
        ]
    )

    assert args.skip_primary_ingestion is True
    assert args.date_from == date(2026, 7, 28)
    assert args.date_to == date(2026, 7, 29)


def test_slice_gets_database_specific_opensearch_namespace() -> None:
    config = OpenSearchConfig(
        base_url="https://search.example.test",
        index_name="procurement_acts",
        index_prefix="procintel",
    )

    scoped = _slice_opensearch_config(
        config,
        "postgresql://localhost/procintel_slice_20260728_20260729_v8",
    )

    assert scoped is not None
    assert scoped.index_prefix == "procintel_procintel_slice_20260728_20260729_v8"
    assert scoped.index_name == (
        "procintel_procintel_slice_20260728_20260729_v8_procurement_acts"
    )
    assert scoped.catalog_index_name("documents") == (
        "procintel_procintel_slice_20260728_20260729_v8_documents"
    )


def test_non_slice_keeps_configured_opensearch_namespace() -> None:
    config = OpenSearchConfig(
        base_url="https://search.example.test",
        index_name="production_acts",
        index_prefix="production",
    )

    assert (
        _slice_opensearch_config(config, "postgresql://localhost/procintel")
        == config
    )


def test_slice_verdict_does_not_fail_on_safely_quarantined_quality_errors() -> None:
    coverage = {
        "source_records": [],
        "enrichment_jobs": [],
        "data_quality": [
            {
                "severity": "ERROR",
                "status": "OPEN",
                "count": 81,
            }
        ],
        "quality_gate": {
            "open_errors": 81,
            "quarantined_errors": 81,
            "unquarantined_errors": 0,
        },
        "spatial_capabilities": [],
    }

    verdict = _build_verdict(coverage, {}, {})

    assert verdict["status"] == "COMPLETE"
    assert verdict["open_quality_errors"] == 81
    assert verdict["quarantined_quality_errors"] == 81
    assert verdict["unquarantined_quality_errors"] == 0

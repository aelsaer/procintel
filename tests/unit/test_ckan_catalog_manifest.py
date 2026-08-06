from __future__ import annotations

from services.ingestion.connectors.ckan.catalog_manifest import (
    CURATED_SUPPLEMENTARY_DATASETS,
    catalog_resource_provenance,
    catalog_resource_quality_issue,
    choose_catalog_resource,
)
from services.ingestion.connectors.ckan.client import PackageShowResponse


def _package(resources: list[dict]) -> PackageShowResponse:
    return PackageShowResponse(
        catalog_dataset_id="example",
        title="Example",
        publisher="Public authority",
        license_code="cc-by",
        resources=resources,
        raw_result={},
    )


def test_curated_manifest_never_claims_primary_or_complete_coverage() -> None:
    assert {item.domain for item in CURATED_SUPPLEMENTARY_DATASETS} >= {
        "GEMI",
        "ANAPTYXI",
        "HEALTH_REFERENCE",
    }
    for item in CURATED_SUPPLEMENTARY_DATASETS:
        assert item.primary_source is False
        assert item.geographic_scope
        assert item.temporal_scope
        assert item.completeness_claim in {
            "AGGREGATE_ONLY",
            "METADATA_ONLY",
            "DOCUMENTATION_ONLY",
            "HISTORICAL_SNAPSHOT",
        }


def test_catalog_resource_selection_prefers_machine_readable_data() -> None:
    resource_url, resource_type = choose_catalog_resource(
        _package(
            [
                {"url": "https://example.test/page", "format": "HTML"},
                {"url": "https://example.test/archive.zip", "format": "ZIP"},
                {"url": "https://example.test/data.csv", "format": "CSV"},
            ]
        )
    )

    assert resource_url == "https://example.test/data.csv"
    assert resource_type == "CSV"


def test_catalog_resource_selection_handles_metadata_without_download() -> None:
    assert choose_catalog_resource(_package([])) == (None, None)
    assert catalog_resource_provenance(_package([]), None) == {
        "resource_availability": "NO_RESOURCE_DECLARED"
    }


def test_catalog_resource_provenance_preserves_freshness_and_size() -> None:
    package = _package(
        [
            {
                "id": "resource-1",
                "url": "https://example.test/data.csv",
                "format": "CSV",
                "size": 0,
                "last_modified": "2026-02-18T06:16:13",
            }
        ]
    )

    assert catalog_resource_provenance(package, "https://example.test/data.csv") == {
        "resource_availability": "UNVALIDATED_METADATA_ONLY",
        "resource_id": "resource-1",
        "resource_size_bytes": 0,
        "resource_last_modified": "2026-02-18T06:16:13",
    }
    assert catalog_resource_quality_issue(
        catalog_resource_provenance(package, "https://example.test/data.csv")
    ) == ("CATALOG_RESOURCE_EMPTY", "WARNING")
    assert catalog_resource_quality_issue({"resource_size_bytes": "1"}) == (
        "CATALOG_RESOURCE_EMPTY",
        "WARNING",
    )

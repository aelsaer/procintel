import inspect

from scripts.finalize_data_plane import _build_parser, _coverage, _verdict


def test_finalize_parser_supports_bounded_resume_stages() -> None:
    args = _build_parser().parse_args(
        [
            "--database-url",
            "postgresql://localhost/procintel_candidate",
            "--geospatial-limit",
            "30000",
            "--skip-search",
        ]
    )

    assert args.geospatial_limit == 30000
    assert args.geospatial_batch_size == 250
    assert args.skip_search is True
    assert args.skip_quality is False


def test_finalize_verdict_ignores_evidence_placeholders_but_blocks_real_errors() -> None:
    coverage = {
        "data_quality": [
            {
                "issue_code": "INCOMPLETE_ADAMCHAIN_PLACEHOLDER",
                "severity": "ERROR",
                "count": 100,
            },
            {"issue_code": "INVALID_DATE_RANGE", "severity": "ERROR", "count": 2},
        ],
        "enrichment_queue": [],
        "geospatial_queue": [],
    }

    assert _verdict({}, coverage) == ("PARTIAL", ["open_quality_errors:2"])


def test_finalize_verdict_accepts_errors_when_quality_gate_quarantines_them() -> None:
    coverage = {
        "data_quality": [
            {"issue_code": "LIFECYCLE_DATE_ORDER", "severity": "ERROR", "count": 80},
            {"issue_code": "INVALID_AFM_CHECKSUM", "severity": "ERROR", "count": 364},
        ],
        "quality_gate": {
            "open_errors": 444,
            "quarantined_errors": 444,
            "unquarantined_errors": 0,
        },
        "enrichment_queue": [],
        "geospatial_queue": [],
    }

    assert _verdict({}, coverage) == ("COMPLETE", [])


def test_finalize_verdict_blocks_quality_errors_that_leak_from_quarantine() -> None:
    coverage = {
        "data_quality": [],
        "quality_gate": {"open_errors": 4, "unquarantined_errors": 1},
        "enrichment_queue": [],
        "geospatial_queue": [],
    }

    assert _verdict({}, coverage) == ("PARTIAL", ["open_quality_errors:1"])


def test_finalize_coverage_excludes_synthetic_source_records() -> None:
    assert "source.source_system <> 'TEST'" in inspect.getsource(_coverage)

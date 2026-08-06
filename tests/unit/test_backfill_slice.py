from datetime import date

from scripts.backfill_slice import _build_parser


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

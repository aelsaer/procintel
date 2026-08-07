from pathlib import Path


SQL_PATH = Path(__file__).resolve().parents[2] / "db" / "marts" / "procurement_360.sql"


def test_process_360_inlines_shared_act_aggregation_for_point_lookups() -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")

    assert "acts_agg AS NOT MATERIALIZED" in sql
    assert "ix_acts_process" in sql

from pathlib import Path

from services.ingestion.orchestration.cli import (
    DEFAULT_DAILY_GEOSPATIAL_MAX_JOBS,
)


def test_default_geospatial_budget_can_absorb_observed_daily_slice_volume() -> None:
    assert DEFAULT_DAILY_GEOSPATIAL_MAX_JOBS == 40000


def test_container_scheduler_defaults_match_capacity_constants() -> None:
    root = Path(__file__).parents[2]
    compose = (root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")
    example = (root / "infra/docker/.env.scheduler.example").read_text(
        encoding="utf-8"
    )
    production_example = (
        root / "infra/docker/.env.production.example"
    ).read_text(encoding="utf-8")

    assert "DAILY_GEOSPATIAL_MAX_JOBS:-40000" in compose
    assert "DAILY_GEMI_MAX_LOOKUPS:-4000" in compose
    assert "DAILY_GEOSPATIAL_MAX_JOBS=40000" in example
    assert "DAILY_GEMI_MAX_LOOKUPS=4000" in example
    assert "DAILY_GEOSPATIAL_MAX_JOBS=40000" in production_example
    assert "DAILY_GEMI_MAX_LOOKUPS=4000" in production_example

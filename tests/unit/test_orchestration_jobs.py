"""`default_jobs()` registry logic — which jobs get registered vs. skipped
based on env config, without touching a real scheduler run."""

import pytest

from services.ingestion.orchestration.jobs import default_jobs


@pytest.fixture(autouse=True)
def _clear_relevant_env(monkeypatch):
    for var in (
        "KHMDHS_API_BASE_URL",
        "TED_API_BASE_URL",
        "OPENSEARCH_URL",
        "GEMI_API_KEY",
        "ANAPTYXI_API_BASE_URL",
        "ANAPTYXI_2007_2013_API_BASE_URL",
        "ANAPTYXI_2014_2020_API_BASE_URL",
        "ANAPTYXI_2021_2027_API_BASE_URL",
        "VIES_API_BASE_URL",
        "DAILY_INGEST_LOOKBACK_DAYS",
        "DAILY_INGEST_MIN_INTERVAL_HOURS",
        "DAILY_DIAVGEIA_MAX_LOOKUPS",
        "DAILY_GEMI_MAX_LOOKUPS",
        "DAILY_MEF_MAX_LOOKUPS",
        "DAILY_ANAPTYXI_MAX_LOOKUPS_PER_PERIOD",
        "DAILY_VIES_MAX_LOOKUPS",
        "TED_BENCHMARK_COUNTRIES",
        "TED_COUNTRIES_PER_CYCLE",
        "TED_DAILY_LOOKBACK_DAYS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_public_defaults_register_both_jobs_without_env():
    jobs, skip_reasons = default_jobs()
    assert jobs[0].source_system == "KHMDHS"
    assert [job.source_system for job in jobs[1:]] == ["TED"] * 5
    assert jobs[0].rolling_lookback_days == 3
    assert jobs[1].partition_key == "GR"
    assert all(job.rolling_lookback_days >= 3 for job in jobs[1:])
    assert not any("job skipped" in reason for reason in skip_reasons)
    assert any("GEMI enrichment inactive" in reason for reason in skip_reasons)
    assert not any("VIES enrichment inactive" in reason for reason in skip_reasons)


def test_khmdhs_override_keeps_ted_public_default(monkeypatch):
    monkeypatch.setenv("KHMDHS_API_BASE_URL", "https://khmdhs.example.test")
    jobs, skip_reasons = default_jobs()
    assert jobs[0].source_system == "KHMDHS"
    assert {job.source_system for job in jobs[1:]} == {"TED"}
    assert not any("job skipped" in reason for reason in skip_reasons)


def test_both_configured_registers_both_jobs(monkeypatch):
    monkeypatch.setenv("KHMDHS_API_BASE_URL", "https://khmdhs.example.test")
    monkeypatch.setenv("TED_API_BASE_URL", "https://ted.example.test")
    jobs, skip_reasons = default_jobs()
    assert {job.source_system for job in jobs} == {"KHMDHS", "TED"}
    assert not any("job skipped" in reason for reason in skip_reasons)


def test_ted_override_keeps_khmdhs_public_default(monkeypatch):
    monkeypatch.setenv("TED_API_BASE_URL", "https://ted.example.test")
    jobs, skip_reasons = default_jobs()
    assert jobs[0].source_system == "KHMDHS"
    assert {job.source_system for job in jobs[1:]} == {"TED"}
    assert not any("job skipped" in reason for reason in skip_reasons)


def test_optional_provider_configuration_is_attached_to_daily_jobs(monkeypatch):
    monkeypatch.setenv("GEMI_API_KEY", "test-key")
    monkeypatch.setenv("ANAPTYXI_2014_2020_API_BASE_URL", "https://anaptyxi.example.test")
    monkeypatch.setenv("VIES_API_BASE_URL", "https://vies.example.test")
    monkeypatch.setenv("DAILY_INGEST_LOOKBACK_DAYS", "5")

    jobs, skip_reasons = default_jobs()

    assert jobs[0].rolling_lookback_days == 5
    assert all(job.rolling_lookback_days >= 5 for job in jobs[1:])
    assert not any("GEMI enrichment inactive" in reason for reason in skip_reasons)
    assert not any("ANAPTYXI_2014_2020 enrichment inactive" in reason for reason in skip_reasons)
    assert not any("VIES enrichment inactive" in reason for reason in skip_reasons)


def test_ted_country_batch_has_independent_cursors_and_gap_safe_lookback(monkeypatch):
    monkeypatch.setenv("TED_BENCHMARK_COUNTRIES", "GR,PT,ES,IT,CY")
    monkeypatch.setenv("TED_COUNTRIES_PER_CYCLE", "3")
    jobs, _ = default_jobs()
    ted_jobs = [job for job in jobs if job.source_system == "TED"]

    assert len(ted_jobs) == 3
    assert ted_jobs[0].partition_key == "GR"
    assert len({job.partition_key for job in ted_jobs}) == 3
    assert all(job.rolling_lookback_days >= 4 for job in ted_jobs)

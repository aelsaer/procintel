import pytest

from services.ingestion.connectors.anaptyxi.config import AnaptyxiConnectorConfig


def test_from_env_reads_period_specific_var(monkeypatch):
    monkeypatch.delenv("ANAPTYXI_API_BASE_URL", raising=False)
    monkeypatch.setenv("ANAPTYXI_2007_2013_API_BASE_URL", "https://2007-2013.example.test")

    config = AnaptyxiConnectorConfig.from_env(program_period="ANAPTYXI_2007_2013")

    assert config.base_url == "https://2007-2013.example.test"
    assert config.program_period == "ANAPTYXI_2007_2013"


def test_from_env_default_period_falls_back_to_legacy_unsuffixed_var(monkeypatch):
    monkeypatch.delenv("ANAPTYXI_2014_2020_API_BASE_URL", raising=False)
    monkeypatch.setenv("ANAPTYXI_API_BASE_URL", "https://legacy.example.test")

    config = AnaptyxiConnectorConfig.from_env()

    assert config.base_url == "https://legacy.example.test"
    assert config.program_period == "ANAPTYXI_2014_2020"


def test_from_env_period_specific_var_takes_priority_over_legacy(monkeypatch):
    monkeypatch.setenv("ANAPTYXI_API_BASE_URL", "https://legacy.example.test")
    monkeypatch.setenv("ANAPTYXI_2014_2020_API_BASE_URL", "https://2014-2020.example.test")

    config = AnaptyxiConnectorConfig.from_env()

    assert config.base_url == "https://2014-2020.example.test"


def test_from_env_missing_var_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("ANAPTYXI_API_BASE_URL", raising=False)
    monkeypatch.delenv("ANAPTYXI_2021_2027_API_BASE_URL", raising=False)

    with pytest.raises(RuntimeError):
        AnaptyxiConnectorConfig.from_env(program_period="ANAPTYXI_2021_2027")


def test_from_env_unknown_period_raises_value_error():
    with pytest.raises(ValueError):
        AnaptyxiConnectorConfig.from_env(program_period="ANAPTYXI_1999_2000")

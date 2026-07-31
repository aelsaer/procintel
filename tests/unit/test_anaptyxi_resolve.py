from services.ingestion.connectors.anaptyxi.resolve import (
    _candidate_mis_values,
)


def test_candidate_mis_values_extracts_only_plausible_project_codes() -> None:
    assert _candidate_mis_values("MIS 6012187") == ["6012187"]
    assert _candidate_mis_values("ΟΠΣ5055803") == ["5055803"]
    assert _candidate_mis_values("ΤΑ 5165728") == ["5165728"]
    assert _candidate_mis_values("1") == []
    assert _candidate_mis_values("60.6481.05002, 60.6481.05003") == []


def test_candidate_mis_values_preserves_documented_symbolic_fixture() -> None:
    assert _candidate_mis_values("OPS-0001") == ["OPS-0001"]

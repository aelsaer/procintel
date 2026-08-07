import inspect

from apps.api.routers.analytics import market_overview


def test_market_overview_inlines_quality_gate_and_preaggregates_geography() -> None:
    source = inspect.getsource(market_overview)

    assert "JOIN source_records source" in source
    assert "NOT EXISTS (" in source
    assert "BOOL_OR(geom IS NOT NULL)" in source
    assert "NOT CAST(:has_taxonomy_filter AS BOOLEAN)" in source
    assert "procintel_act_is_analytics_eligible(a.id)" not in source

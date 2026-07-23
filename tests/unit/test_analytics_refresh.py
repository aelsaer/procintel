import pytest

from services.analytics.refresh import MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER, refresh_mart


def test_dependency_order_puts_market_hhi_after_its_two_inputs():
    order = MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER
    assert order.index("market_value_metrics") < order.index("market_hhi")
    assert order.index("supplier_market_share") < order.index("market_hhi")


def test_dependency_order_puts_renewal_signals_after_cycle_time_metrics():
    order = MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER
    assert order.index("cycle_time_metrics") < order.index("renewal_signals")


def test_all_ten_expected_marts_are_present_exactly_once():
    expected = {
        "market_value_metrics",
        "supplier_market_share",
        "market_hhi",
        "buyer_concentration",
        "supplier_dependency",
        "incumbent_signals",
        "contract_modification_stats",
        "cycle_time_metrics",
        "payment_execution",
        "renewal_signals",
    }
    assert set(MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER) == expected
    assert len(MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER) == len(expected)


async def test_refresh_mart_rejects_a_name_outside_the_whitelist():
    # guards against SQL injection via the f-string REFRESH statement —
    # only ever a fixed whitelisted identifier, never caller-supplied text
    with pytest.raises(ValueError):
        await refresh_mart(conn=None, mart_name="pg_shadow; DROP TABLE users; --")

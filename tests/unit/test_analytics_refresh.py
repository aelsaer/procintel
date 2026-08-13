import pytest

import services.analytics.refresh as refresh_module
from services.analytics.refresh import MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER, refresh_all_marts, refresh_mart


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


class _RecoverableConnection:
    def __init__(self):
        self.aborted = False
        self.rollbacks = 0
        self.commits = 0

    async def execute(self, _statement, _parameters=None):
        if self.aborted:
            raise RuntimeError("transaction is aborted")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.aborted = False
        self.rollbacks += 1

    def in_transaction(self):
        return self.aborted


async def test_refresh_recovers_failed_transaction_and_skips_dependent_mart(monkeypatch):
    conn = _RecoverableConnection()
    attempted: list[str] = []

    async def locked(_conn, _key):
        return True

    async def unlocked(_conn, _key):
        return None

    async def fail_first(_conn, mart_name):
        attempted.append(mart_name)
        if mart_name == "market_value_metrics":
            conn.aborted = True
            raise RuntimeError("refresh failed")

    monkeypatch.setattr(refresh_module, "try_advisory_lock", locked)
    monkeypatch.setattr(refresh_module, "advisory_unlock", unlocked)
    monkeypatch.setattr(refresh_module, "refresh_mart", fail_first)

    outcomes = await refresh_all_marts(conn)

    by_name = {outcome.mart_name: outcome for outcome in outcomes}
    assert conn.rollbacks == 1
    assert not by_name["market_value_metrics"].succeeded
    assert not by_name["market_hhi"].succeeded
    assert "dependencies failed" in (by_name["market_hhi"].error or "")
    assert "market_hhi" not in attempted
    assert by_name["buyer_concentration"].succeeded

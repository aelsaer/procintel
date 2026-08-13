import uuid

from packages.tenancy import tenant_session


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Connection:
    def __init__(self):
        self.calls = []
        self.settings = iter(["previous-tenant", "PREVIOUS_ROLE"])
        self.rolled_back = False

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if "current_setting" in str(statement):
            return _Result(next(self.settings))
        return _Result()

    def in_transaction(self):
        return True

    async def rollback(self):
        self.rolled_back = True


async def test_tenant_session_sets_and_restores_worker_rls_context():
    conn = _Connection()
    tenant_id = uuid.uuid4()

    async with tenant_session(conn, tenant_id):
        assert conn.calls[2][1] == {"tenant_id": str(tenant_id)}
        assert conn.calls[3][1] == {"role": "WORKER"}

    assert conn.calls[-2][1] == {"tenant_id": "previous-tenant"}
    assert conn.calls[-1][1] == {"role": "PREVIOUS_ROLE"}
    assert conn.rolled_back is False


async def test_tenant_session_rolls_back_before_restoring_after_failure():
    conn = _Connection()
    try:
        async with tenant_session(conn, uuid.uuid4()):
            raise RuntimeError("failed job")
    except RuntimeError:
        pass
    assert conn.rolled_back is True

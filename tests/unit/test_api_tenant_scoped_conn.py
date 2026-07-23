"""`get_tenant_scoped_conn`'s tenant_id validation happens before it ever
touches the DB engine, so the missing/malformed-claim rejection paths are
unit-testable without DATABASE_URL set. The happy path (actually opening a
connection and setting `app.tenant_id`) needs a real Postgres instance —
see tests/integration/test_rls_enforcement_db.py.
"""

import pytest
from fastapi import HTTPException

from apps.api.db import get_tenant_scoped_conn
from packages.auth.jwt_verifier import AuthenticatedUser


async def test_rejects_a_token_with_no_tenant_id_claim():
    user = AuthenticatedUser(subject="u1", email=None, tenant_id=None, role="ANALYST")
    with pytest.raises(HTTPException) as exc_info:
        async for _ in get_tenant_scoped_conn(user=user):
            pass
    assert exc_info.value.status_code == 400


async def test_rejects_a_tenant_id_claim_that_is_not_a_uuid():
    user = AuthenticatedUser(subject="u1", email=None, tenant_id="not-a-uuid", role="ANALYST")
    with pytest.raises(HTTPException) as exc_info:
        async for _ in get_tenant_scoped_conn(user=user):
            pass
    assert exc_info.value.status_code == 400

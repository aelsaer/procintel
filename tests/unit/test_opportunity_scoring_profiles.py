import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from services.analytics.opportunity_scoring import (
    tenant_ids_with_business_profiles,
)


@pytest.mark.asyncio
async def test_tenant_ids_with_business_profiles_uses_persisted_profiles_only() -> None:
    tenant_ids = [uuid.uuid4(), uuid.uuid4()]
    scalar_result = Mock()
    scalar_result.all.return_value = tenant_ids
    result = Mock()
    result.scalars.return_value = scalar_result
    conn = AsyncMock()
    conn.execute.return_value = result

    assert await tenant_ids_with_business_profiles(conn) == tenant_ids

    statement = conn.execute.await_args.args[0]
    assert "business_profiles.tenant_id" in str(statement)
    assert "tenants" not in str(statement)

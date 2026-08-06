from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from services.ingestion.connectors.mef.client import MefUpstreamUnavailableError
from services.ingestion.enrichment_queue import ClaimedEnrichmentJob
from services.ingestion.enrichment_worker import (
    ProviderUpstreamContractError,
    _dispatch,
)


async def test_mef_outage_blocks_provider_for_the_rest_of_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args, **kwargs) -> int:
        raise MefUpstreamUnavailableError("MEF timed out")

    monkeypatch.setattr(
        "services.ingestion.enrichment_worker.resolve_expenses_for_contractor",
        unavailable,
    )
    dependencies = SimpleNamespace(
        mef=object(),
        raw_store=object(),
        runtime_unavailable_providers=set(),
    )
    job = ClaimedEnrichmentJob(
        id=uuid.uuid4(),
        provider="MEF",
        idempotency_key="mef:090000045",
        payload={"entity_id": str(uuid.uuid4()), "afm": "090000045"},
        object_type="entity",
        object_id=None,
        source_record_id=None,
        attempt_count=1,
        max_attempts=8,
    )

    with pytest.raises(ProviderUpstreamContractError, match="MEF timed out"):
        await _dispatch(object(), dependencies, job)

    assert dependencies.runtime_unavailable_providers == {"MEF"}


@pytest.mark.parametrize("provider", ("GEMI", "MEF"))
async def test_invalid_afm_is_completed_without_calling_provider(provider: str) -> None:
    dependencies = SimpleNamespace(
        gemi_provider=object(),
        mef=object(),
        raw_store=object(),
        runtime_unavailable_providers=set(),
    )
    job = ClaimedEnrichmentJob(
        id=uuid.uuid4(),
        provider=provider,
        idempotency_key=f"{provider.lower()}:0945510036",
        payload={"entity_id": str(uuid.uuid4()), "afm": "0945510036"},
        object_type="entity",
        object_id=None,
        source_record_id=None,
        attempt_count=1,
        max_attempts=8,
    )

    result = await _dispatch(object(), dependencies, job)

    assert result == {"skipped_invalid_afm": True, "_external_call": False}

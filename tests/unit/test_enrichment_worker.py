from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from services.ingestion.connectors.mef.client import MefUpstreamUnavailableError
from services.ingestion.enrichment_queue import ClaimedEnrichmentJob
from services.ingestion.enrichment_worker import (
    ProviderUpstreamContractError,
    _dispatch,
    run_pending_enrichment_jobs,
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


async def test_upstream_contract_failure_opens_provider_circuit_for_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "ANAPTYXI_2021_2027"
    job = ClaimedEnrichmentJob(
        id=uuid.uuid4(),
        provider=provider,
        idempotency_key="anaptyxi:blocked",
        payload={"act_id": str(uuid.uuid4())},
        object_type="act",
        object_id=None,
        source_record_id=None,
        attempt_count=1,
        max_attempts=8,
    )
    claim_calls = 0

    class FakeDependencies:
        upstream_errors: dict[str, str] = {}
        runtime_unavailable_providers: set[str] = set()
        known_providers = {provider}
        available_providers = {provider}

        def __init__(self, raw_root: str) -> None:
            self.runtime_unavailable_providers = set()

        async def aclose(self) -> None:
            return None

    class EmptyRows:
        def all(self) -> list[object]:
            return []

    class FakeConnection:
        async def execute(self, statement):
            return EmptyRows()

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def claim_once(*args, **kwargs):
        nonlocal claim_calls
        claim_calls += 1
        return [job]

    async def fail(*args, **kwargs) -> None:
        return None

    async def recover(*args, **kwargs) -> int:
        return 0

    async def dispatch(*args, **kwargs):
        raise ProviderUpstreamContractError("provider contract unavailable")

    monkeypatch.setattr(
        "services.ingestion.enrichment_worker._Dependencies", FakeDependencies
    )
    monkeypatch.setattr(
        "services.ingestion.enrichment_worker.claim_enrichment_jobs", claim_once
    )
    monkeypatch.setattr(
        "services.ingestion.enrichment_worker.fail_enrichment", fail
    )
    monkeypatch.setattr(
        "services.ingestion.enrichment_worker.recover_stale_enrichment_jobs", recover
    )
    monkeypatch.setattr("services.ingestion.enrichment_worker._dispatch", dispatch)

    result = await run_pending_enrichment_jobs(
        FakeConnection(),
        raw_root="/tmp/raw",
        limit=10,
        providers={provider},
        provider_budgets={provider: 10},
    )

    assert claim_calls == 1
    assert result.blocked_upstream == 1

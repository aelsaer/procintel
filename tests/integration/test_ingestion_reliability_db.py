from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    act_identifiers,
    act_links,
    act_parties,
    data_quality_issues,
    enrichment_jobs,
    entities,
    procurement_acts,
    procurement_processes,
    source_records,
)
from services.ingestion.enrichment_reconciliation import (
    enqueue_process_diavgeia_search_jobs,
)
from services.ingestion.enrichment_queue import (
    claim_enrichment_jobs,
    enqueue_enrichment,
    fail_enrichment,
)
from services.ingestion.enrichment_worker import _block_static_upstream_jobs
from services.ingestion.connectors.inspire.capabilities import (
    _capability_source_record_insert,
)
from services.data_quality.service import run_data_quality_checks

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL is required for integration tests",
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_inspire_capability_evidence_dedupes_by_global_content_hash() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    content_hash = uuid.uuid4().hex
    first_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            common = {
                "source_system": "INSPIRE",
                "resource_type": "WMS_CAPABILITIES",
                "content_sha256": content_hash,
                "payload_uri": f"mem://{content_hash}",
                "fetched_at": datetime.now(timezone.utc),
                "parse_status": "PARSED",
            }
            inserted = (
                await conn.execute(
                    _capability_source_record_insert(
                        {
                            **common,
                            "id": first_id,
                            "source_native_id": "https://example.test/first/wms",
                        }
                    )
                )
            ).scalar_one()
            duplicate = (
                await conn.execute(
                    _capability_source_record_insert(
                        {
                            **common,
                            "id": uuid.uuid4(),
                            "source_native_id": "https://example.test/alias/wms",
                        }
                    )
                )
            ).scalar_one_or_none()

            assert inserted == first_id
            assert duplicate is None
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                source_records.delete().where(
                    source_records.c.source_system == "INSPIRE",
                    source_records.c.resource_type == "WMS_CAPABILITIES",
                    source_records.c.content_sha256 == content_hash,
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_blocked_provider_waits_for_configuration_and_can_reactivate() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    source_record_id = uuid.uuid4()
    key = f"test-gemi-{uuid.uuid4()}"
    try:
        async with engine.connect() as conn:
            await conn.execute(
                source_records.insert().values(
                    id=source_record_id,
                    source_system="KHMDHS",
                    resource_type="contract",
                    source_native_id=key,
                    content_sha256=uuid.uuid4().hex,
                    payload_uri=f"/tmp/{key}.json",
                    fetched_at=datetime.now(timezone.utc),
                    parse_status="PARSED",
                )
            )
            ref = await enqueue_enrichment(
                conn,
                provider="GEMI",
                idempotency_key=key,
                payload={"afm": "094000000", "entity_id": str(uuid.uuid4())},
                source_record_id=source_record_id,
            )
            await conn.commit()

            claimed = await claim_enrichment_jobs(
                conn,
                limit=10,
                providers={"GEMI"},
            )
            assert [job.id for job in claimed] == [ref.id]
            await fail_enrichment(
                conn,
                ref.id,
                error={"message": "GEMI_API_KEY is not configured"},
                blocked_config=True,
            )
            await conn.commit()

            assert not await claim_enrichment_jobs(
                conn,
                limit=10,
                providers={"GEMI"},
            )
            still_blocked = await enqueue_enrichment(
                conn,
                provider="GEMI",
                idempotency_key=key,
                payload={"afm": "094000000", "entity_id": str(uuid.uuid4())},
                source_record_id=source_record_id,
            )
            await conn.commit()
            assert still_blocked.status == "BLOCKED_CONFIG"
            reactivated = await claim_enrichment_jobs(
                conn,
                limit=10,
                providers={"GEMI"},
                reactivate_blocked_providers={"GEMI"},
            )
            assert [job.id for job in reactivated] == [ref.id]
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                enrichment_jobs.delete().where(
                    enrichment_jobs.c.idempotency_key == key
                )
            )
            await conn.execute(
                source_records.delete().where(source_records.c.id == source_record_id)
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_upstream_blocked_provider_can_reactivate_after_contract_fix() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    key = f"test-upstream-{uuid.uuid4()}"
    try:
        async with engine.connect() as conn:
            ref = await enqueue_enrichment(
                conn,
                provider="ANAPTYXI_2021_2027",
                idempotency_key=key,
                payload={"act_id": str(uuid.uuid4())},
            )
            await conn.commit()
            assert await claim_enrichment_jobs(
                conn,
                limit=1,
                providers={"ANAPTYXI_2021_2027"},
            )
            await fail_enrichment(
                conn,
                ref.id,
                error={"type": "ProviderUpstreamContractError"},
                blocked_upstream=True,
            )
            await conn.commit()

            assert not await claim_enrichment_jobs(
                conn,
                limit=1,
                providers={"ANAPTYXI_2021_2027"},
            )
            reactivated = await claim_enrichment_jobs(
                conn,
                limit=1,
                providers={"ANAPTYXI_2021_2027"},
                reactivate_blocked_providers={"ANAPTYXI_2021_2027"},
            )
            assert [job.id for job in reactivated] == [ref.id]
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                enrichment_jobs.delete().where(
                    enrichment_jobs.c.idempotency_key == key
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_static_upstream_contract_error_bulk_blocks_pending_queue() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    provider = "ANAPTYXI_2021_2027"
    key_prefix = f"test-upstream-bulk-{uuid.uuid4()}"
    job_ids = [uuid.uuid4() for _ in range(4)]
    try:
        async with engine.connect() as conn:
            for index, status in enumerate(
                ("QUEUED", "FAILED", "BLOCKED_CONFIG", "BLOCKED_UPSTREAM")
            ):
                await conn.execute(
                    enrichment_jobs.insert().values(
                        id=job_ids[index],
                        provider=provider,
                        idempotency_key=f"{key_prefix}-{index}",
                        payload={"act_id": str(uuid.uuid4())},
                        status=status,
                    )
                )
            await conn.commit()

            blocked = await _block_static_upstream_jobs(
                conn,
                {provider: "validated API contract unavailable"},
                providers={provider},
            )

            rows = (
                await conn.execute(
                    sa.select(
                        enrichment_jobs.c.status,
                        enrichment_jobs.c.last_error,
                    )
                    .where(enrichment_jobs.c.id.in_(job_ids))
                    .order_by(enrichment_jobs.c.created_at, enrichment_jobs.c.id)
                )
            ).all()
            assert blocked == {provider: 3}
            assert {row.status for row in rows} == {"BLOCKED_UPSTREAM"}
            assert sum(
                row.last_error
                == {
                    "type": "ProviderUpstreamContractError",
                    "message": "validated API contract unavailable",
                }
                for row in rows
            ) == 3
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                enrichment_jobs.delete().where(
                    enrichment_jobs.c.idempotency_key.like(f"{key_prefix}%")
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_reenqueue_resets_terminal_provider_attempts() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    key = f"test-reset-{uuid.uuid4()}"
    try:
        async with engine.connect() as conn:
            ref = await enqueue_enrichment(
                conn,
                provider="GEMI",
                idempotency_key=key,
                payload={"afm": "094000000"},
                max_attempts=1,
            )
            await conn.commit()
            claimed = await claim_enrichment_jobs(
                conn,
                limit=1,
                providers={"GEMI"},
            )
            assert claimed
            await fail_enrichment(
                conn,
                ref.id,
                error={"message": "temporary failure"},
            )
            await conn.commit()
            terminal_status = (
                await conn.execute(
                    sa.select(enrichment_jobs.c.status).where(
                        enrichment_jobs.c.id == ref.id
                    )
                )
            ).scalar_one()
            assert terminal_status == "DEAD"

            reopened = await enqueue_enrichment(
                conn,
                provider="GEMI",
                idempotency_key=key,
                payload={"afm": "094000000"},
                max_attempts=1,
            )
            await conn.commit()
            assert reopened.status == "QUEUED"
            assert reopened.attempt_count == 0
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                enrichment_jobs.delete().where(
                    enrichment_jobs.c.idempotency_key == key
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_permanent_failure_stays_dead_when_reenqueued() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    key = f"test-permanent-{uuid.uuid4()}"
    try:
        async with engine.connect() as conn:
            ref = await enqueue_enrichment(
                conn,
                provider="KHMDHS_DOCUMENT",
                idempotency_key=key,
                payload={"adam": key},
            )
            await conn.commit()
            assert await claim_enrichment_jobs(
                conn,
                limit=1,
                providers={"KHMDHS_DOCUMENT"},
            )
            await fail_enrichment(
                conn,
                ref.id,
                error={"type": "PdfPageLimitExceededError"},
                permanent=True,
            )
            await conn.commit()

            unchanged = await enqueue_enrichment(
                conn,
                provider="KHMDHS_DOCUMENT",
                idempotency_key=key,
                payload={"adam": key},
            )
            await conn.commit()
            assert unchanged.status == "DEAD"
            assert unchanged.attempt_count == 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                enrichment_jobs.delete().where(
                    enrichment_jobs.c.idempotency_key == key
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_available_retry_is_claimed_before_older_queued_work() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    queued_key = f"test-queued-{uuid.uuid4()}"
    retry_key = f"test-retry-{uuid.uuid4()}"
    try:
        async with engine.connect() as conn:
            await enqueue_enrichment(
                conn,
                provider="TEST_QUEUED",
                idempotency_key=queued_key,
                payload={},
            )
            retry_ref = await enqueue_enrichment(
                conn,
                provider="TEST_RETRY",
                idempotency_key=retry_key,
                payload={},
            )
            await conn.commit()
            claimed_retry = await claim_enrichment_jobs(
                conn,
                limit=1,
                providers={"TEST_RETRY"},
            )
            assert [job.id for job in claimed_retry] == [retry_ref.id]
            await fail_enrichment(
                conn,
                retry_ref.id,
                error={"type": "TransientServerError"},
            )
            await conn.execute(
                enrichment_jobs.update()
                .where(enrichment_jobs.c.id == retry_ref.id)
                .values(available_at=datetime.now(timezone.utc))
            )
            await conn.commit()

            next_job = await claim_enrichment_jobs(
                conn,
                limit=1,
                providers={"TEST_QUEUED", "TEST_RETRY"},
            )
            assert [job.id for job in next_job] == [retry_ref.id]
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                enrichment_jobs.delete().where(
                    enrichment_jobs.c.idempotency_key.in_(
                        [queued_key, retry_key]
                    )
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_data_quality_queries_are_typed_for_asyncpg() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    try:
        async with engine.connect() as conn:
            result = await run_data_quality_checks(
                conn,
                date_from=datetime.now(timezone.utc).date(),
                date_to=datetime.now(timezone.utc).date(),
                repair_invalid_dates=False,
            )
            assert result.invalid_dates_repaired == 0
            assert set(result.by_code) == {
                "INVALID_AFM_CHECKSUM",
                "INVALID_DATE_RANGE",
                "INCOMPLETE_ADAMCHAIN_PLACEHOLDER",
                "END_BEFORE_START",
                "GROSS_BELOW_NET",
                "NEGATIVE_PROCUREMENT_VALUE",
                "IMPLAUSIBLE_PROCUREMENT_VALUE",
                "LIFECYCLE_DATE_ORDER",
                "MISSING_EVENT_DATE",
                "MISSING_CPV",
                "MISSING_SUPPLIER",
                "MISSING_OFFICIAL_DOCUMENT",
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bad_values_and_lifecycle_order_are_quarantined() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    source_ids = [uuid.uuid4(), uuid.uuid4()]
    notice_id = uuid.uuid4()
    award_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                source_records.insert(),
                [
                    {
                        "id": source_id,
                        "source_system": "TEST",
                        "resource_type": "quality_gate",
                        "source_native_id": f"quality-{source_id}",
                        "content_sha256": uuid.uuid4().hex,
                        "payload_uri": f"/tmp/quality-{source_id}.json",
                        "fetched_at": datetime.now(timezone.utc),
                        "parse_status": "PARSED",
                    }
                    for source_id in source_ids
                ],
            )
            await conn.execute(
                procurement_acts.insert(),
                [
                    {
                        "id": notice_id,
                        "act_type": "NOTICE",
                        "publication_date": datetime(2026, 7, 29).date(),
                        "decision_date": None,
                        "amount_net": 2_000_000_000_000,
                        "amount_gross": 2_480_000_000_000,
                        "source_record_id": source_ids[0],
                    },
                    {
                        "id": award_id,
                        "act_type": "AWARD",
                        "publication_date": None,
                        "decision_date": datetime(2026, 7, 28).date(),
                        "amount_net": -100,
                        "amount_gross": -124,
                        "source_record_id": source_ids[1],
                    },
                ],
            )
            await conn.execute(
                act_links.insert().values(
                    id=uuid.uuid4(),
                    from_act_id=award_id,
                    to_act_id=notice_id,
                    link_type="AWARDS",
                    link_method="TEST",
                    confidence=1,
                    evidence={},
                    created_by="TEST",
                )
            )

        async with engine.connect() as conn:
            await run_data_quality_checks(
                conn,
                date_from=datetime(2026, 7, 28).date(),
                date_to=datetime(2026, 7, 29).date(),
                repair_invalid_dates=False,
            )
            rows = (
                await conn.execute(
                    sa.select(
                        data_quality_issues.c.object_id,
                        data_quality_issues.c.issue_code,
                    ).where(
                        data_quality_issues.c.object_id.in_([notice_id, award_id]),
                        data_quality_issues.c.severity == "ERROR",
                        data_quality_issues.c.status == "OPEN",
                    )
                )
            ).all()
            codes_by_act = {
                act_id: {row.issue_code for row in rows if row.object_id == act_id}
                for act_id in (notice_id, award_id)
            }
            assert "IMPLAUSIBLE_PROCUREMENT_VALUE" in codes_by_act[notice_id]
            assert {
                "NEGATIVE_PROCUREMENT_VALUE",
                "LIFECYCLE_DATE_ORDER",
            }.issubset(codes_by_act[award_id])
            assert not (
                await conn.execute(
                    sa.text("SELECT procintel_act_is_analytics_eligible(:act_id)"),
                    {"act_id": award_id},
                )
            ).scalar_one()
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                data_quality_issues.delete().where(
                    data_quality_issues.c.object_id.in_([notice_id, award_id])
                )
            )
            await conn.execute(
                act_links.delete().where(
                    act_links.c.from_act_id == award_id,
                    act_links.c.to_act_id == notice_id,
                )
            )
            await conn.execute(
                procurement_acts.delete().where(
                    procurement_acts.c.id.in_([notice_id, award_id])
                )
            )
            await conn.execute(
                source_records.delete().where(source_records.c.id.in_(source_ids))
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_adamchain_evidence_is_quarantined_from_analytics() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    source_id = uuid.uuid4()
    act_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                source_records.insert().values(
                    id=source_id,
                    source_system="KHMDHS",
                    resource_type="adamChain",
                    source_native_id=f"placeholder-{act_id}",
                    content_sha256=uuid.uuid4().hex,
                    payload_uri=f"/tmp/placeholder-{act_id}.json",
                    fetched_at=datetime.now(timezone.utc),
                    parse_status="PARSED",
                )
            )
            await conn.execute(
                procurement_acts.insert().values(
                    id=act_id,
                    act_type="NOTICE",
                    source_record_id=source_id,
                )
            )

        async with engine.connect() as conn:
            result = await run_data_quality_checks(
                conn,
                date_from=datetime.now(timezone.utc).date(),
                date_to=datetime.now(timezone.utc).date(),
                repair_invalid_dates=False,
            )
            issue = (
                await conn.execute(
                    sa.select(data_quality_issues.c.severity).where(
                        data_quality_issues.c.object_id == act_id,
                        data_quality_issues.c.issue_code
                        == "INCOMPLETE_ADAMCHAIN_PLACEHOLDER",
                        data_quality_issues.c.status == "OPEN",
                    )
                )
            ).scalar_one()
            eligible = (
                await conn.execute(
                    sa.text(
                        "SELECT procintel_act_is_analytics_eligible(:act_id)"
                    ),
                    {"act_id": act_id},
                )
            ).scalar_one()

            assert result.by_code["INCOMPLETE_ADAMCHAIN_PLACEHOLDER"] == 1
            assert issue == "ERROR"
            assert eligible is False
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                data_quality_issues.delete().where(
                    data_quality_issues.c.object_id == act_id
                )
            )
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id == act_id))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_synthetic_source_act_is_quarantined_only_when_flagged() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    source_id = uuid.uuid4()
    act_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                source_records.insert().values(
                    id=source_id,
                    source_system="TEST",
                    resource_type="pagination_seed",
                    source_native_id=f"synthetic-{act_id}",
                    content_sha256=uuid.uuid4().hex,
                    payload_uri=f"/tmp/synthetic-{act_id}.json",
                    fetched_at=datetime.now(timezone.utc),
                    parse_status="PARSED",
                )
            )
            await conn.execute(
                procurement_acts.insert().values(
                    id=act_id,
                    act_type="CONTRACT",
                    source_record_id=source_id,
                    title="Synthetic contract",
                    publication_date=datetime.now(timezone.utc).date(),
                )
            )

        async with engine.connect() as conn:
            eligible_before_quarantine = (
                await conn.execute(
                    sa.text("SELECT procintel_act_is_analytics_eligible(:act_id)"),
                    {"act_id": act_id},
                )
            ).scalar_one()
            assert eligible_before_quarantine is True

        async with engine.begin() as conn:
            await conn.execute(
                data_quality_issues.insert().values(
                    id=uuid.uuid4(),
                    source_record_id=source_id,
                    object_type="procurement_act",
                    object_id=act_id,
                    issue_code="TEST_SOURCE_RECORD_IN_PRODUCTION",
                    severity="ERROR",
                    details={"source_system": "TEST"},
                    status="OPEN",
                )
            )

        async with engine.connect() as conn:
            eligible_after_quarantine = (
                await conn.execute(
                    sa.text("SELECT procintel_act_is_analytics_eligible(:act_id)"),
                    {"act_id": act_id},
                )
            ).scalar_one()
            assert eligible_after_quarantine is False
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                data_quality_issues.delete().where(
                    data_quality_issues.c.object_id == act_id
                )
            )
            await conn.execute(
                procurement_acts.delete().where(procurement_acts.c.id == act_id)
            )
            await conn.execute(
                source_records.delete().where(source_records.c.id == source_id)
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_diavgeia_fallback_is_queued_once_per_process() -> None:
    engine = create_async_engine(_async_url(DATABASE_URL))
    source_record_id = uuid.uuid4()
    buyer_id = uuid.uuid4()
    process_id = uuid.uuid4()
    notice_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    adam_values = [f"26PROC{uuid.uuid4().int % 10**9:09d}", f"26SYMV{uuid.uuid4().int % 10**9:09d}"]
    try:
        async with engine.begin() as conn:
            await conn.execute(
                source_records.insert().values(
                    id=source_record_id,
                    source_system="KHMDHS",
                    resource_type="notice",
                    source_native_id=adam_values[0],
                    content_sha256=uuid.uuid4().hex,
                    payload_uri=f"/tmp/{adam_values[0]}.json",
                    fetched_at=datetime.now(timezone.utc),
                    parse_status="PARSED",
                )
            )
            await conn.execute(
                entities.insert().values(
                    id=buyer_id,
                    entity_type="PUBLIC_BODY",
                    canonical_name="TEST PROCESS BUYER",
                    normalized_name="TEST PROCESS BUYER",
                )
            )
            await conn.execute(
                procurement_processes.insert().values(
                    id=process_id,
                    public_id=f"proc_{uuid.uuid4().hex[:20]}",
                )
            )
            await conn.execute(
                procurement_acts.insert(),
                [
                    {
                        "id": notice_id,
                        "process_id": process_id,
                        "act_type": "NOTICE",
                        "title": "Test notice",
                        "source_record_id": source_record_id,
                    },
                    {
                        "id": contract_id,
                        "process_id": process_id,
                        "act_type": "CONTRACT",
                        "title": "Test contract",
                        "source_record_id": source_record_id,
                    },
                ],
            )
            await conn.execute(
                act_identifiers.insert(),
                [
                    {
                        "id": uuid.uuid4(),
                        "act_id": notice_id,
                        "scheme": "ADAM",
                        "value_raw": adam_values[0],
                        "value_normalized": adam_values[0],
                        "source_record_id": source_record_id,
                    },
                    {
                        "id": uuid.uuid4(),
                        "act_id": contract_id,
                        "scheme": "ADAM",
                        "value_raw": adam_values[1],
                        "value_normalized": adam_values[1],
                        "source_record_id": source_record_id,
                    },
                ],
            )
            await conn.execute(
                act_parties.insert(),
                [
                    {
                        "id": uuid.uuid4(),
                        "act_id": notice_id,
                        "entity_id": buyer_id,
                        "party_role": "BUYER",
                        "source_record_id": source_record_id,
                    },
                    {
                        "id": uuid.uuid4(),
                        "act_id": contract_id,
                        "entity_id": buyer_id,
                        "party_role": "BUYER",
                        "source_record_id": source_record_id,
                    },
                ],
            )

        async with engine.connect() as conn:
            assert await enqueue_process_diavgeia_search_jobs(
                conn, process_ids={process_id}
            ) == 1
            assert await enqueue_process_diavgeia_search_jobs(
                conn, process_ids={process_id}
            ) == 1
            rows = (
                await conn.execute(
                    sa.select(enrichment_jobs).where(
                        enrichment_jobs.c.provider == "DIAVGEIA_SEARCH",
                        enrichment_jobs.c.idempotency_key == str(process_id),
                    )
                )
            ).all()
            assert len(rows) == 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                enrichment_jobs.delete().where(
                    enrichment_jobs.c.idempotency_key == str(process_id)
                )
            )
            await conn.execute(
                act_parties.delete().where(
                    act_parties.c.act_id.in_([notice_id, contract_id])
                )
            )
            await conn.execute(
                act_identifiers.delete().where(
                    act_identifiers.c.act_id.in_([notice_id, contract_id])
                )
            )
            await conn.execute(
                procurement_acts.delete().where(
                    procurement_acts.c.id.in_([notice_id, contract_id])
                )
            )
            await conn.execute(
                procurement_processes.delete().where(
                    procurement_processes.c.id == process_id
                )
            )
            await conn.execute(entities.delete().where(entities.c.id == buyer_id))
            await conn.execute(
                source_records.delete().where(
                    source_records.c.id == source_record_id
                )
            )
        await engine.dispose()

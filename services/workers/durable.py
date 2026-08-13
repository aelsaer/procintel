"""Poll and drain user-triggered jobs that must survive API restarts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from packages.tenancy import all_tenant_ids, tenant_session
from services.alerts.digests import process_due_digests
from services.alerts.factory import build_delivery_channel
from services.alerts.webhook_delivery import retry_pending_deliveries
from services.analytics.scoring_worker import process_pending_scoring_jobs
from services.bids.reminders import deliver_due_reminders
from services.exports.generate import process_pending_export_jobs
from services.ingestion.on_demand import process_pending_fetch_requests


@dataclass(frozen=True)
class DurableWorkerResult:
    fetch_requests: int
    scoring_jobs: int
    export_jobs: int
    digests: int
    webhook_retries: int
    reminders: int


def _async_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")
    return database_url


async def run_durable_jobs_once(
    engine: AsyncEngine,
    *,
    raw_root: str,
    fetch_limit: int = 10,
    export_limit: int = 20,
) -> DurableWorkerResult:
    fetch_count = await process_pending_fetch_requests(
        engine,
        raw_root=raw_root,
        limit=fetch_limit,
    )
    async with engine.connect() as conn:
        scoring_count = await process_pending_scoring_jobs(conn)
        export_count = await process_pending_export_jobs(conn, limit=export_limit)
        digest_count = 0
        webhook_retry_count = 0
        reminder_count = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            delivery_channel = build_delivery_channel(client)
            for tenant_id in await all_tenant_ids(conn):
                async with tenant_session(conn, tenant_id):
                    digest_result = await process_due_digests(
                        conn,
                        delivery_channel=delivery_channel,
                    )
                    digest_count += digest_result.digests_created
                    webhook_retry_count += await retry_pending_deliveries(conn, client)
            # The reminder service already performs its own RLS-aware tenant
            # sweep. Calling it inside the loop above makes the work O(n^2).
            reminder_result = await deliver_due_reminders(conn, client)
            reminder_count += reminder_result["processed"]
    return DurableWorkerResult(
        fetch_requests=fetch_count,
        scoring_jobs=scoring_count,
        export_jobs=export_count,
        digests=digest_count,
        webhook_retries=webhook_retry_count,
        reminders=reminder_count,
    )


async def run_durable_worker(
    database_url: str,
    *,
    raw_root: str,
    poll_interval_seconds: float = 5.0,
) -> None:
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    engine = create_async_engine(_async_url(database_url), pool_pre_ping=True)
    try:
        while True:
            try:
                result = await run_durable_jobs_once(engine, raw_root=raw_root)
                if any(result.__dict__.values()):
                    print(
                        "durable jobs: "
                        f"fetch={result.fetch_requests} "
                        f"scoring={result.scoring_jobs} "
                        f"exports={result.export_jobs} "
                        f"digests={result.digests} "
                        f"webhooks={result.webhook_retries} "
                        f"reminders={result.reminders}"
                    )
            except Exception as exc:  # noqa: BLE001 - unattended worker must recover next poll
                print(f"durable worker cycle: FAILED -> {type(exc).__name__}: {exc}")
            await asyncio.sleep(poll_interval_seconds)
    finally:
        await engine.dispose()

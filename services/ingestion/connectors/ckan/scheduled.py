"""Whole-dataset refresh scheduling for CKAN/data.gov.gr.

Deliberately not a `services/ingestion/orchestration/scheduler.py`
`ScheduledJob`: that abstraction is date-windowed (a `source_cursors` row
advances by calendar day), which fits ΚΗΜΔΗΣ/TED's "list changes since X"
APIs but not CKAN's "redownload this whole file" ones — a dataset either
needs refreshing or it doesn't, there's no date range to compute.
`external_datasets` is the watermark here instead of `source_cursors`:
`last_seen_at` (set by `registry.py::upsert_external_dataset` on every
sync) plus a fixed `DEFAULT_REFRESH_INTERVAL` decide whether a dataset is
due. `update_frequency` is deliberately not consulted — it's a free-text
column no `_sync_*` call has ever populated, so trusting it would mean
inventing an unconfirmed vocabulary rather than reading real data.

Each `_sync_*` function in `cli.py` owns its own engine/connection
lifecycle (`create_async_engine(database_url)` internally) rather than
accepting an existing `AsyncConnection` — refactoring that is out of scope
here, so this module takes `database_url` too and only uses its own `conn`
argument for the `external_datasets` due-check and the advisory lock. The
lock is session-scoped (`pg_lock.py`) and held on *this* connection for
the duration of one dataset's dispatch, which correctly serializes
concurrent schedulers even though the actual sync work happens over a
separate, short-lived connection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import external_datasets
from packages.source_clients.pg_lock import advisory_unlock, try_advisory_lock

from .cli import (
    DEFAULT_DATASETS,
    _sync_boundaries,
    _sync_facilities,
    _sync_metric,
    _sync_population,
)

_logger = logging.getLogger(__name__)

DEFAULT_REFRESH_INTERVAL = timedelta(days=7)


async def onboard_default_ckan_datasets(
    conn: AsyncConnection,
    *,
    database_url: str,
    raw_root: str = "./raw",
) -> list[str]:
    """Onboard maintained national datasets once, with live validation."""
    existing = set(
        (
            await conn.execute(
                select(external_datasets.c.catalog_dataset_id).where(
                    external_datasets.c.catalog_dataset_id.in_(
                        [dataset["dataset_id"] for dataset in DEFAULT_DATASETS]
                    )
                )
            )
        ).scalars()
    )
    onboarded: list[str] = []
    for dataset in DEFAULT_DATASETS:
        dataset_id = dataset["dataset_id"]
        if dataset_id in existing:
            continue
        if dataset["adapter"] == "boundaries":
            await _sync_boundaries(
                dataset_id,
                dataset["boundary_type"],
                database_url,
                raw_root,
            )
        elif dataset["adapter"] == "facilities":
            await _sync_facilities(
                dataset_id,
                dataset["facility_type"],
                dataset.get("capacity_metric"),
                dataset.get("capacity_field"),
                database_url,
                raw_root,
            )
        else:
            raise ValueError(f"unknown default CKAN adapter {dataset['adapter']!r}")
        onboarded.append(dataset_id)
    return onboarded


@dataclass(frozen=True)
class DatasetRefreshOutcome:
    external_dataset_id: UUID
    catalog_dataset_id: str
    adapter_name: str
    ran: bool
    skipped_reason: str | None = None
    error: str | None = None


def _is_due(last_seen_at: datetime | None, *, now: datetime, interval: timedelta) -> bool:
    return last_seen_at is None or (now - last_seen_at) >= interval


async def _dispatch(row: Any, *, database_url: str, raw_root: str) -> None:
    config = row.config or {}
    if row.adapter_name == "population":
        await _sync_population(row.catalog_dataset_id, config["reference_year"], database_url, raw_root)
    elif row.adapter_name == "boundaries":
        await _sync_boundaries(row.catalog_dataset_id, config["boundary_type"], database_url, raw_root)
    elif row.adapter_name == "metric":
        await _sync_metric(
            row.catalog_dataset_id,
            config["metric_name"],
            config["reference_year"],
            config.get("value_field"),
            database_url,
            raw_root,
        )
    elif row.adapter_name == "facilities":
        await _sync_facilities(
            row.catalog_dataset_id,
            config["facility_type"],
            config.get("capacity_metric"),
            config.get("capacity_field"),
            database_url,
            raw_root,
        )
    else:
        raise ValueError(f"unknown external_datasets.adapter_name {row.adapter_name!r}")


async def refresh_due_ckan_datasets(
    conn: AsyncConnection,
    *,
    database_url: str,
    raw_root: str = "./raw",
    interval: timedelta = DEFAULT_REFRESH_INTERVAL,
    now: datetime | None = None,
) -> list[DatasetRefreshOutcome]:
    """Scans onboarded `external_datasets` rows and re-syncs whichever are
    due, dispatching by `adapter_name` to the matching `cli.py::_sync_*`
    function with its stored `config` unpacked as kwargs. A dataset is
    onboarded once via the manual CLI (`cli.py sync-population`/etc, an
    operator action per that module's docstring) — after that, this
    function keeps it fresh without a human re-running the CLI by hand.
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        await conn.execute(select(external_datasets).where(external_datasets.c.ingestion_status == "ONBOARDED"))
    ).all()

    outcomes: list[DatasetRefreshOutcome] = []
    for row in rows:
        if not _is_due(row.last_seen_at, now=now, interval=interval):
            outcomes.append(
                DatasetRefreshOutcome(
                    external_dataset_id=row.id,
                    catalog_dataset_id=row.catalog_dataset_id,
                    adapter_name=row.adapter_name,
                    ran=False,
                    skipped_reason="not due",
                )
            )
            continue

        lock_key = f"procintel:orchestration:CKAN:{row.catalog_dataset_id}"
        if not await try_advisory_lock(conn, lock_key):
            outcomes.append(
                DatasetRefreshOutcome(
                    external_dataset_id=row.id,
                    catalog_dataset_id=row.catalog_dataset_id,
                    adapter_name=row.adapter_name,
                    ran=False,
                    skipped_reason="locked by another scheduler",
                )
            )
            continue
        try:
            try:
                await _dispatch(row, database_url=database_url, raw_root=raw_root)
            except Exception as exc:  # noqa: BLE001 — one dataset's failure must not block the rest
                _logger.exception("CKAN dataset refresh failed for %s", row.catalog_dataset_id)
                outcomes.append(
                    DatasetRefreshOutcome(
                        external_dataset_id=row.id,
                        catalog_dataset_id=row.catalog_dataset_id,
                        adapter_name=row.adapter_name,
                        ran=False,
                        error=str(exc),
                    )
                )
                continue
            outcomes.append(
                DatasetRefreshOutcome(
                    external_dataset_id=row.id,
                    catalog_dataset_id=row.catalog_dataset_id,
                    adapter_name=row.adapter_name,
                    ran=True,
                )
            )
        finally:
            await advisory_unlock(conn, lock_key)

    return outcomes

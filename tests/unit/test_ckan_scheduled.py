"""`refresh_due_ckan_datasets` — the whole-dataset-refresh scheduler for
CKAN, deliberately not a `ScheduledJob` (see the module docstring). Pure
`_is_due` logic plus dispatch-by-`adapter_name` behavior, monkeypatched so
no real HTTP/database is needed.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import services.ingestion.connectors.ckan.scheduled as scheduled_module
from services.ingestion.connectors.ckan.scheduled import DEFAULT_REFRESH_INTERVAL, _dispatch, _is_due


def test_never_synced_dataset_is_due():
    assert _is_due(None, now=datetime.now(timezone.utc), interval=DEFAULT_REFRESH_INTERVAL)


def test_recently_synced_dataset_is_not_due():
    now = datetime.now(timezone.utc)
    last_seen_at = now - timedelta(days=1)
    assert not _is_due(last_seen_at, now=now, interval=DEFAULT_REFRESH_INTERVAL)


def test_stale_dataset_is_due():
    now = datetime.now(timezone.utc)
    last_seen_at = now - timedelta(days=8)
    assert _is_due(last_seen_at, now=now, interval=DEFAULT_REFRESH_INTERVAL)


async def test_dispatch_routes_population_with_stored_config(monkeypatch):
    calls = []

    async def _fake_sync_population(dataset_id, reference_year, database_url, raw_root):
        calls.append((dataset_id, reference_year, database_url, raw_root))

    monkeypatch.setattr(scheduled_module, "_sync_population", _fake_sync_population)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        catalog_dataset_id="plithysmos-dimon-2021",
        adapter_name="population",
        config={"reference_year": 2021},
    )
    await _dispatch(row, database_url="postgresql://x", raw_root="./raw")
    assert calls == [("plithysmos-dimon-2021", 2021, "postgresql://x", "./raw")]


async def test_dispatch_routes_facilities_with_optional_capacity_metric(monkeypatch):
    calls = []

    async def _fake_sync_facilities(dataset_id, facility_type, capacity_metric, capacity_field, database_url, raw_root):
        calls.append((dataset_id, facility_type, capacity_metric, capacity_field, database_url, raw_root))

    monkeypatch.setattr(scheduled_module, "_sync_facilities", _fake_sync_facilities)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        catalog_dataset_id="sxoleia-2024",
        adapter_name="facilities",
        config={
            "facility_type": "SCHOOL",
            "capacity_metric": "STUDENTS",
            "capacity_field": "students_total",
        },
    )
    await _dispatch(row, database_url="postgresql://x", raw_root="./raw")
    assert calls == [
        (
            "sxoleia-2024",
            "SCHOOL",
            "STUDENTS",
            "students_total",
            "postgresql://x",
            "./raw",
        )
    ]


async def test_dispatch_raises_on_unknown_adapter_name():
    row = SimpleNamespace(id=uuid.uuid4(), catalog_dataset_id="mystery", adapter_name="unknown", config={})
    try:
        await _dispatch(row, database_url="postgresql://x", raw_root="./raw")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown" in str(exc)

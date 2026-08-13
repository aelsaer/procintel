"""ΓΕΜΗ enrichment against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Covers the parts that
can't be verified without a real database: the temporal snapshot transition
(§18.2 — never overwrite, close out the old row and open a new one), the
refresh-policy gate actually preventing/allowing API calls (backdating
`source_records.fetched_at` directly rather than mocking the clock, so the
test exercises the same `should_refresh()` logic the connector runs), and
the negative-result-is-not-permanent behavior (§18.3).
"""

import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import entities, entity_company_snapshots, entity_identifiers, source_records
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.gemi.cache import ACTIVE_REFRESH, NEGATIVE_RESULT_REFRESH
from services.ingestion.connectors.gemi.client import GemiClient
from services.ingestion.connectors.gemi.config import GemiConnectorConfig
from services.ingestion.connectors.gemi.provider import GemiCompanyRegistryProvider
from services.ingestion.connectors.gemi.resolve import resolve_company_snapshot

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gemi" / "company_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BASE_URL = "https://gemi.example.test"
AFM = "090000057"
AR_GEMI = 123456789057
SAMPLE_BODY.update({"afm": AFM, "arGemi": AR_GEMI})


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def _make_company_entity(conn) -> uuid.UUID:
    entity_id = uuid.uuid4()
    await conn.execute(
        entities.insert().values(id=entity_id, entity_type="COMPANY", canonical_name=AFM, normalized_name=AFM)
    )
    await conn.execute(
        entity_identifiers.insert().values(
            id=uuid.uuid4(),
            entity_id=entity_id,
            scheme="AFM",
            value_raw=AFM,
            value_normalized=AFM,
            country_code="GR",
        )
    )
    await conn.commit()
    return entity_id


async def _backdate_last_gemi_check(conn, *, days: int) -> None:
    await conn.execute(
        text(
            "UPDATE source_records SET fetched_at = fetched_at - make_interval(days => :days) "
            "WHERE source_system = 'GEMI'"
        ),
        {"days": days},
    )
    await conn.commit()


@respx.mock
async def test_snapshot_lifecycle_refresh_gate_and_negative_result(tmp_path):
    client = GemiClient(GemiConnectorConfig(base_url=BASE_URL, api_key="test-key", rate_limit_per_minute=6000))
    provider = GemiCompanyRegistryProvider(client)
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            entity_id = await _make_company_entity(conn)

            # 1. first lookup: no cache yet -> always refreshes -> writes a snapshot
            route = respx.get(
                f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
            ).mock(
                return_value=httpx.Response(200, json={"searchResults": [SAMPLE_BODY]})
            )
            respx.get(f"{BASE_URL}/companies/{AR_GEMI}/documents").mock(
                return_value=httpx.Response(200, json={"decision": [], "publication": []})
            )
            wrote = await resolve_company_snapshot(
                conn, provider=provider, raw_store=raw_store, afm_normalized=AFM, entity_id=entity_id
            )
            assert wrote.wrote_new_snapshot is True
            # a brand-new company has no prior snapshot to report a status
            # change from — old_status is None even though a row was written
            assert wrote.old_status is None
            assert wrote.new_status == "ACTIVE"
            assert route.call_count == 1

            snapshots = (
                await conn.execute(
                    select(entity_company_snapshots).where(entity_company_snapshots.c.entity_id == entity_id)
                )
            ).all()
            assert len(snapshots) == 1
            assert snapshots[0].is_current is True
            assert snapshots[0].company_status == "ACTIVE"  # normalized via lexicon.py, not the raw "ΕΝΕΡΓΗ" label

            gemi_identifier = (
                await conn.execute(
                    select(entity_identifiers).where(
                        entity_identifiers.c.entity_id == entity_id, entity_identifiers.c.scheme == "GEMI"
                    )
                )
            ).one()
            assert gemi_identifier.value_normalized == str(AR_GEMI)

            # 2. immediate re-check: refresh policy gates it — no second API call
            wrote_again = await resolve_company_snapshot(
                conn, provider=provider, raw_store=raw_store, afm_normalized=AFM, entity_id=entity_id
            )
            assert wrote_again.wrote_new_snapshot is False
            assert route.call_count == 1  # unchanged — the gate, not a dedup-after-fetch, prevented the call

            # 3. simulate 31 days passing (stable-status window is 30) -> the gate
            # opens, a real re-check happens, and the status actually changed ->
            # a NEW snapshot row, with the old one closed out (§18.2)
            await _backdate_last_gemi_check(conn, days=ACTIVE_REFRESH.days + 1)

            changed_body = dict(SAMPLE_BODY, status={"id": 2, "descr": "ΥΠΟ ΕΚΚΑΘΑΡΙΣΗ"})
            respx.get(
                f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
            ).mock(
                return_value=httpx.Response(200, json={"searchResults": [changed_body]})
            )
            wrote_changed = await resolve_company_snapshot(
                conn, provider=provider, raw_store=raw_store, afm_normalized=AFM, entity_id=entity_id
            )
            assert wrote_changed.wrote_new_snapshot is True
            assert wrote_changed.old_status == "ACTIVE"
            assert wrote_changed.new_status == "IN_LIQUIDATION"  # normalized, not the raw "ΥΠΟ ΕΚΚΑΘΑΡΙΣΗ" label

            all_snapshots = (
                await conn.execute(
                    select(entity_company_snapshots)
                    .where(entity_company_snapshots.c.entity_id == entity_id)
                    .order_by(entity_company_snapshots.c.observed_at)
                )
            ).all()
            assert len(all_snapshots) == 2
            assert all_snapshots[0].is_current is False
            assert all_snapshots[0].valid_to is not None
            assert all_snapshots[0].company_status == "ACTIVE"
            assert all_snapshots[1].is_current is True
            assert all_snapshots[1].valid_to is None
            assert all_snapshots[1].company_status == "IN_LIQUIDATION"
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_negative_result_is_rechecked_after_window_not_cached_forever(tmp_path):
    client = GemiClient(GemiConnectorConfig(base_url=BASE_URL, api_key="test-key", rate_limit_per_minute=6000))
    provider = GemiCompanyRegistryProvider(client)
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())
    afm = f"99{uuid.uuid4().int % 10_000_000:07d}"  # unique per run; always 404s below

    try:
        async with engine.connect() as conn:
            entity_id = uuid.uuid4()
            await conn.execute(
                entities.insert().values(id=entity_id, entity_type="COMPANY", canonical_name=afm, normalized_name=afm)
            )
            await conn.commit()

            route = respx.get(
                f"{BASE_URL}/companies", params={"afm": afm, "resultsSize": "1"}
            ).mock(
                return_value=httpx.Response(404)
            )

            wrote = await resolve_company_snapshot(
                conn, provider=provider, raw_store=raw_store, afm_normalized=afm, entity_id=entity_id
            )
            assert wrote.wrote_new_snapshot is False
            assert route.call_count == 1

            not_found_rows = (
                await conn.execute(
                    select(source_records).where(
                        source_records.c.source_system == "GEMI",
                        source_records.c.resource_type == "company_not_found",
                        source_records.c.source_native_id == afm,
                    )
                )
            ).all()
            assert len(not_found_rows) == 1

            # immediate recheck: gated, no second call
            await resolve_company_snapshot(
                conn, provider=provider, raw_store=raw_store, afm_normalized=afm, entity_id=entity_id
            )
            assert route.call_count == 1

            # after the negative-result window: rechecked, not cached forever
            await _backdate_last_gemi_check(conn, days=NEGATIVE_RESULT_REFRESH.days + 1)
            await resolve_company_snapshot(
                conn, provider=provider, raw_store=raw_store, afm_normalized=afm, entity_id=entity_id
            )
            assert route.call_count == 2
    finally:
        await client.aclose()
        await engine.dispose()

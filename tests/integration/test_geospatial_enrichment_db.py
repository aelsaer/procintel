"""Trigger -> extractor -> local gazetteer -> PostGIS -> analytics API."""

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    act_locations,
    geospatial_enrichment_jobs,
    procurement_acts,
    source_records,
)
from services.geospatial.geonames import GazetteerPlace
from services.geospatial.service import (
    ClaimedJob,
    enqueue_existing_acts,
    process_job,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _asyncpg_url() -> str:
    assert DATABASE_URL
    return (
        "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://") :]
        if DATABASE_URL.startswith("postgresql://")
        else DATABASE_URL
    )


async def test_geospatial_enrichment_and_locations_api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    raw = {
        "referenceNumber": "26PROC-GEO-TEST",
        "title": "Δοκιμαστική προκήρυξη",
        "nutsCity": "ΔΟΚΙΜΟΧΩΡΙ",
        "nutsPostalCode": "12345",
        "nutsCode": {"key": "EL303", "value": "Κεντρικός Τομέας Αθηνών"},
        "objectDetailsList": [{"city": "ΔΟΚΙΜΟΧΩΡΙ"}],
    }
    raw_path = tmp_path / "geo-record.json"
    raw_bytes = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    raw_path.write_bytes(raw_bytes)

    source_id, act_id = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            await conn.execute(
                source_records.insert().values(
                    id=source_id,
                    source_system="KHMDHS",
                    resource_type="notice",
                    source_native_id="26PROC-GEO-TEST",
                    content_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                    payload_uri=str(raw_path),
                    fetched_at=datetime.now(timezone.utc),
                    parse_status="PARSED",
                )
            )
            await conn.execute(
                procurement_acts.insert().values(
                    id=act_id,
                    act_type="NOTICE",
                    title=raw["title"],
                    normalized_title=raw["title"].upper(),
                    publication_date=date.today(),
                    source_record_id=source_id,
                )
            )
            await conn.commit()

            job_row = (
                await conn.execute(
                    sa.select(geospatial_enrichment_jobs).where(
                        geospatial_enrichment_jobs.c.act_id == act_id,
                        geospatial_enrichment_jobs.c.source_record_id == source_id,
                    )
                )
            ).one()
            result = await process_job(
                conn,
                ClaimedJob(job_row.id, act_id, source_id, 1),
                admin_units=[],
                gazetteer_places=[
                    GazetteerPlace(
                        geoname_id=999999999,
                        country_code="GR",
                        name="Δοκιμοχώρι",
                        normalized_names=("ΔΟΚΙΜΟΧΩΡΙ",),
                        admin_name_1="Αττική",
                        admin_code_1="I",
                        admin_name_2="Κεντρικός Τομέας Αθηνών",
                        admin_code_2="A1",
                        admin_name_3=None,
                        admin_code_3=None,
                        feature_class="P",
                        feature_code="PPL",
                        population=1000,
                        latitude=37.9838,
                        longitude=23.7275,
                    )
                ],
                remote=None,
            )
            await conn.commit()
            assert result.status == "SUCCEEDED"

            await conn.execute(
                geospatial_enrichment_jobs.update()
                .where(geospatial_enrichment_jobs.c.id == job_row.id)
                .values(
                    status="PARTIAL",
                    attempt_count=5,
                    locked_at=datetime.now(timezone.utc),
                    locked_by="stale-worker",
                    last_error={"type": "PreviousParserFailure"},
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await conn.commit()
            await enqueue_existing_acts(conn, requeue_partial=True)
            replayed_job = (
                await conn.execute(
                    sa.select(geospatial_enrichment_jobs).where(
                        geospatial_enrichment_jobs.c.id == job_row.id
                    )
                )
            ).one()
            assert replayed_job.status == "QUEUED"
            assert replayed_job.attempt_count == 0
            assert replayed_job.locked_at is None
            assert replayed_job.locked_by is None
            assert replayed_job.last_error is None
            assert replayed_job.finished_at is None

            location = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT municipality_name, regional_unit_name, region_name,
                               ST_Y(geom) AS latitude, ST_X(geom) AS longitude,
                               geocode_provider
                        FROM act_locations
                        WHERE act_id = :act_id AND enrichment_job_id IS NOT NULL
                        """
                    ),
                    {"act_id": str(act_id)},
                )
            ).one()
            assert location.municipality_name == "ΔΟΚΙΜΟΧΩΡΙ"
            assert location.regional_unit_name == "Κεντρικός Τομέας Αθηνών"
            assert location.geocode_provider == "GEONAMES"
            assert location.latitude == pytest.approx(37.9838)

        from apps.api.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/v1/analytics/locations",
                params={"date_from": date.today().isoformat(), "date_to": date.today().isoformat()},
            )
        assert response.status_code == 200
        point = next(item for item in response.json() if item["label"] == "ΔΟΚΙΜΟΧΩΡΙ")
        assert point["opportunity_count"] == 1
        assert point["latitude"] == pytest.approx(37.9838)
    finally:
        async with engine.connect() as conn:
            await conn.execute(act_locations.delete().where(act_locations.c.act_id == act_id))
            await conn.execute(geospatial_enrichment_jobs.delete().where(geospatial_enrichment_jobs.c.act_id == act_id))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id == act_id))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
            await conn.commit()
        await engine.dispose()

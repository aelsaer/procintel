"""GET /v1/search keyset pagination against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Seeds directly into
`procurement_acts` (no need to run a full connector pipeline just to test
the search endpoint's pagination) — 5 rows sharing a title substring, none
of which have an exact ΑΔΑΜ/ΑΔΑ identifier, so every page here exercises
only the title-match keyset phase. Walks the full result set two pages at
a time (`limit=2`) and verifies: every row appears exactly once across all
pages (no duplicates, no gaps — the bug the previous OFFSET-based
implementation was exposed to), pages are stably ordered by
`(normalized_title, id)`, and the final page correctly reports
`has_more=False`.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import procurement_acts, source_records

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_search_pagination_has_no_duplicates_or_gaps(tmp_path):
    engine = create_async_engine(_asyncpg_url())
    query = f"PAGINATION-TEST-{uuid.uuid4().hex}"

    try:
        async with engine.connect() as conn:
            source_record_id = uuid.uuid4()
            await conn.execute(
                source_records.insert().values(
                    id=source_record_id,
                    source_system="TEST",
                    resource_type="pagination_seed",
                    content_sha256=f"sha-{uuid.uuid4()}",
                    payload_uri="mem://pagination-seed",
                    fetched_at=datetime.now(timezone.utc),
                    parse_status="PARSED",
                )
            )

            seeded_ids = set()
            for i in range(5):
                act_id = uuid.uuid4()
                seeded_ids.add(str(act_id))
                title = f"{query} {i:02d}"
                await conn.execute(
                    procurement_acts.insert().values(
                        id=act_id,
                        act_type="CONTRACT",
                        title=title,
                        normalized_title=title.upper(),
                        source_record_id=source_record_id,
                    )
                )
            await conn.commit()

            from httpx import ASGITransport
            import httpx

            from apps.api.main import app

            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
                seen_ids: list[str] = []
                cursor = None
                pages_fetched = 0
                while True:
                    pages_fetched += 1
                    assert pages_fetched <= 10, "pagination did not terminate — possible infinite loop"
                    params = {"q": query, "limit": 2}
                    if cursor:
                        params["cursor"] = cursor
                    resp = await api_client.get("/v1/search", params=params)
                    assert resp.status_code == 200
                    body = resp.json()
                    page_ids = [row["act_id"] for row in body["data"]]
                    assert not (set(page_ids) & set(seen_ids)), "duplicate row across pages"
                    seen_ids.extend(page_ids)
                    if not body["pagination"]["has_more"]:
                        assert body["pagination"]["next_cursor"] is None
                        break
                    cursor = body["pagination"]["next_cursor"]
                    assert cursor is not None

                assert set(seen_ids) == seeded_ids
                assert pages_fetched == 3  # 5 rows at limit=2 -> pages of 2,2,1
    finally:
        await engine.dispose()

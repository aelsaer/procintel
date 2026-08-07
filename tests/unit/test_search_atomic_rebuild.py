from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from services.search_index.client import swap_index_aliases
from services.search_index.config import OpenSearchConfig
from services.search_index.indexer import (
    _load_acts_for_indexing,
    _physical_build_config,
)


def test_physical_build_config_keeps_logical_namespace_separate() -> None:
    logical = OpenSearchConfig(
        base_url="https://search.example.test",
        index_name="procurement_acts",
        index_prefix="procintel",
    )

    physical = _physical_build_config(logical, "20260807-test")

    assert physical.index_name == "procurement_acts__20260807_test"
    assert physical.catalog_index_name("documents") == (
        "procintel__20260807_test_documents"
    )
    assert logical.index_name == "procurement_acts"


@pytest.mark.asyncio
async def test_alias_swap_replaces_aliases_and_legacy_concrete_indexes_atomically() -> None:
    captured_actions: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/_alias/acts":
            return httpx.Response(
                200,
                json={"acts__old": {"aliases": {"acts": {}}}},
            )
        if request.method == "GET" and request.url.path == "/_alias/docs":
            return httpx.Response(404)
        if request.method == "HEAD" and request.url.path == "/docs":
            return httpx.Response(200)
        if request.method == "POST" and request.url.path == "/_aliases":
            captured_actions.extend(json.loads(request.content)["actions"])
            return httpx.Response(200, json={"acknowledged": True})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = OpenSearchConfig(base_url="https://search.example.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        old = await swap_index_aliases(
            client,
            config,
            {"acts": "acts__new", "docs": "docs__new"},
        )

    assert old == {"acts__old"}
    assert captured_actions == [
        {"remove": {"index": "acts__old", "alias": "acts"}},
        {"add": {"index": "acts__new", "alias": "acts"}},
        {"remove_index": {"index": "docs"}},
        {"add": {"index": "docs__new", "alias": "docs"}},
    ]


@pytest.mark.asyncio
async def test_act_index_data_is_loaded_in_four_queries_per_batch() -> None:
    act_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    process_id = uuid.uuid4()
    results = iter(
        [
            [SimpleNamespace(act_id=act_id, scheme="ADAM", value_normalized="26PROC1")],
            [
                SimpleNamespace(
                    act_id=act_id,
                    party_role="SUPPLIER",
                    entity_id=entity_id,
                    canonical_name="Supplier SA",
                )
            ],
            [SimpleNamespace(act_id=act_id, cpv_code="72200000")],
            [SimpleNamespace(act_id=act_id, nuts_code="EL30")],
        ]
    )

    class Result:
        def __init__(self, rows) -> None:
            self.rows = rows

        def all(self):
            return self.rows

    class Connection:
        calls = 0

        async def execute(self, statement):
            self.calls += 1
            return Result(next(results))

    conn = Connection()
    acts = await _load_acts_for_indexing(
        conn,
        [
            SimpleNamespace(
                id=act_id,
                process_id=process_id,
                title="GIS services",
                normalized_title="gis services",
                act_type="NOTICE",
                status="OPEN",
                procedure_type="OPEN",
                amount_net=100,
                amount_gross=124,
                currency="EUR",
                submission_date=None,
                decision_date=None,
            )
        ],
    )

    assert conn.calls == 4
    assert acts[0].adam == "26PROC1"
    assert acts[0].cpv_codes == ["72200000"]
    assert acts[0].nuts_codes == ["EL30"]
    assert acts[0].supplier_ids == [str(entity_id)]

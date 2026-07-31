"""Minimal async OpenSearch REST client — index/bulk-index/search only,
built on `httpx` (not the official `opensearch-py` SDK; see the note in
`pyproject.toml` for why). OpenSearch's REST API is plain JSON-over-HTTP
for everything this module needs, so a thin wrapper is enough, and it
keeps this fully `respx`-mockable like every other HTTP-dependent module
in this codebase.
"""

from __future__ import annotations

import json

import httpx

from .config import OpenSearchConfig


def _auth(config: OpenSearchConfig) -> tuple[str, str] | None:
    if config.username and config.password:
        return (config.username, config.password)
    return None


async def index_exists(http_client: httpx.AsyncClient, config: OpenSearchConfig) -> bool:
    response = await http_client.head(f"{config.base_url}/{config.index_name}", auth=_auth(config))
    return response.status_code == 200


async def create_index(http_client: httpx.AsyncClient, config: OpenSearchConfig, mapping: dict) -> None:
    response = await http_client.put(
        f"{config.base_url}/{config.index_name}", json=mapping, auth=_auth(config)
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"failed to create index {config.index_name!r}: {response.status_code} {response.text}")


async def delete_index(http_client: httpx.AsyncClient, config: OpenSearchConfig) -> None:
    await http_client.delete(f"{config.base_url}/{config.index_name}", auth=_auth(config))


async def delete_all_documents(
    http_client: httpx.AsyncClient,
    config: OpenSearchConfig,
) -> int:
    response = await http_client.post(
        f"{config.base_url}/{config.index_name}/_delete_by_query",
        params={"conflicts": "proceed", "refresh": "true"},
        json={"query": {"match_all": {}}},
        auth=_auth(config),
    )
    response.raise_for_status()
    return int(response.json().get("deleted", 0))


async def bulk_index(http_client: httpx.AsyncClient, config: OpenSearchConfig, documents: list[dict]) -> dict:
    """`documents` must each carry an `id` field — used as the OpenSearch
    `_id` so re-indexing the same act is an upsert, not a duplicate."""
    if not documents:
        return {"items": []}

    lines: list[str] = []
    for doc in documents:
        lines.append(json.dumps({"index": {"_index": config.index_name, "_id": doc["id"]}}))
        lines.append(json.dumps(doc, default=str))
    body = "\n".join(lines) + "\n"

    response = await http_client.post(
        f"{config.base_url}/_bulk",
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        auth=_auth(config),
    )
    response.raise_for_status()
    result = response.json()
    if result.get("errors"):
        failed = [item for item in result.get("items", []) if item.get("index", {}).get("status", 200) >= 300]
        raise RuntimeError(f"bulk index had {len(failed)} failing item(s): {failed[:3]}")
    return result


async def search(http_client: httpx.AsyncClient, config: OpenSearchConfig, query_body: dict) -> dict:
    response = await http_client.post(
        f"{config.base_url}/{config.index_name}/_search", json=query_body, auth=_auth(config)
    )
    response.raise_for_status()
    return response.json()

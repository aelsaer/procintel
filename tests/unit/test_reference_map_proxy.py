from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from apps.api.deps import get_http_client
from apps.api.main import app


async def test_reference_map_proxy_forwards_only_allowlisted_wms_parameters() -> None:
    captured: dict[str, str] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\n",
            headers={"content-type": "image/png"},
        )

    async def override_client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream)
        ) as client:
            yield client

    app.dependency_overrides[get_http_client] = override_client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/v1/analytics/reference-map/flood-hazard-high",
                params={
                    "bbox": "2100000,4300000,3200000,5300000",
                    "width": 256,
                    "height": 256,
                    "srs": "EPSG:3857",
                    "layers": "attacker-controlled-layer",
                },
            )
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-source"] == "Greek INSPIRE WMS"
    assert captured["LAYERS"] == "NZ.Flood"
    assert captured["SRS"] == "EPSG:3857"
    assert "attacker-controlled-layer" not in captured.values()


async def test_reference_map_proxy_rejects_unknown_layer_before_upstream() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/v1/analytics/reference-map/not-allowlisted",
            params={"bbox": "1,2,3,4"},
        )

    assert response.status_code == 404

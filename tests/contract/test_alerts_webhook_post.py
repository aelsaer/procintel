"""Outbound webhook POST against mocked HTTP (respx) — signature header
construction and body shape, without a database (that's
`_attempt_delivery`'s own concern; `deliver()`'s DB bookkeeping is covered
by the DATABASE_URL-gated integration test)."""

import json

import httpx
import respx

from services.alerts.webhook_delivery import _attempt_delivery

URL = "https://example.test/incoming-webhook"


@respx.mock
async def test_attempt_delivery_signs_body_and_posts_json():
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        status = await _attempt_delivery(
            client,
            url=URL,
            body={"event_type": "contract.created"},
            secret="shh",
            allow_test_hosts=True,
        )

    assert status == 200
    request = route.calls[0].request
    assert request.headers["X-Procintel-Signature"].startswith("sha256=")
    assert json.loads(request.content) == {"event_type": "contract.created"}


@respx.mock
async def test_attempt_delivery_without_a_secret_sends_no_signature_header():
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await _attempt_delivery(
            client,
            url=URL,
            body={"a": 1},
            secret=None,
            allow_test_hosts=True,
        )

    request = route.calls[0].request
    assert "X-Procintel-Signature" not in request.headers


@respx.mock
async def test_attempt_delivery_returns_the_response_status_on_failure():
    respx.post(URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        status = await _attempt_delivery(
            client,
            url=URL,
            body={"a": 1},
            secret=None,
            allow_test_hosts=True,
        )
    assert status == 500


async def test_attempt_delivery_rejects_private_destinations():
    async with httpx.AsyncClient() as client:
        try:
            await _attempt_delivery(
                client,
                url="http://127.0.0.1/internal",
                body={"a": 1},
                secret=None,
            )
        except ValueError as exc:
            assert "non-public" in str(exc)
        else:  # pragma: no cover - documents the security invariant
            raise AssertionError("private webhook destination was accepted")

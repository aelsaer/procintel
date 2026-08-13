from starlette.requests import Request

from apps.api.main import _client_ip, _request_rate_keys


def _request(*, client: str, internal_client: str | None = None) -> Request:
    headers = []
    if internal_client is not None:
        headers.append((b"x-procintel-client-ip", internal_client.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (client, 12345),
        }
    )


def test_production_rate_limit_uses_sanitized_bff_client_ip(monkeypatch):
    monkeypatch.setenv("PROCINTEL_ENV", "production")
    request = _request(client="10.0.0.5", internal_client="2001:db8::10")

    assert _client_ip(request) == "2001:db8::10"
    assert _request_rate_keys(request) == ("ip:2001:db8::10",)


def test_invalid_internal_client_ip_falls_back_to_peer(monkeypatch):
    monkeypatch.setenv("PROCINTEL_ENV", "production")

    assert _client_ip(_request(client="10.0.0.5", internal_client="spoofed")) == "10.0.0.5"


def test_development_does_not_trust_internal_forwarding_header(monkeypatch):
    monkeypatch.setenv("PROCINTEL_ENV", "development")

    assert _client_ip(_request(client="127.0.0.1", internal_client="203.0.113.9")) == "127.0.0.1"

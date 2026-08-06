from __future__ import annotations

from apps.api.routers.companies import _registry_http_exception
from packages.source_clients.retry import RateLimitedError, TransientServerError
from services.ingestion.connectors.gemi.client import (
    GemiAuthenticationError,
    GemiInvalidResponseError,
)


def test_gemi_authentication_error_is_redacted_service_unavailable() -> None:
    response = _registry_http_exception(GemiAuthenticationError("rejected"))

    assert response.status_code == 503
    assert "κλειδί" not in response.detail.casefold()


def test_gemi_rate_limit_exposes_retry_after_without_upstream_body() -> None:
    response = _registry_http_exception(RateLimitedError(12))

    assert response.status_code == 503
    assert response.headers == {"Retry-After": "12"}


def test_gemi_invalid_json_and_server_errors_have_distinct_statuses() -> None:
    assert _registry_http_exception(GemiInvalidResponseError("bad json")).status_code == 502
    assert _registry_http_exception(TransientServerError("HTTP 503")).status_code == 503

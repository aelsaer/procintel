import pytest

from packages.network_safety import validate_public_http_url


async def _public_resolver(hostname: str, port: int) -> list[str]:
    assert hostname == "documents.gov.gr"
    assert port == 443
    return ["8.8.8.8", "2001:4860:4860::8888"]


async def _mixed_resolver(hostname: str, port: int) -> list[str]:
    return ["8.8.8.8", "127.0.0.1"]


async def test_public_http_url_accepts_only_public_dns_answers():
    await validate_public_http_url(
        "https://documents.gov.gr/tender.pdf",
        resolver=_public_resolver,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/document",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/document",
        "http://[::1]/document",
        "http://localhost/document",
        "ftp://documents.gov.gr/file",
        "https://user:password@documents.gov.gr/file",
        "https://documents.gov.gr:8443/file",
    ],
)
async def test_remote_url_rejects_local_metadata_credentials_and_unsafe_ports(url):
    with pytest.raises(ValueError):
        await validate_public_http_url(url, resolver=_public_resolver)


async def test_remote_url_rejects_hostname_when_any_dns_answer_is_private():
    with pytest.raises(ValueError):
        await validate_public_http_url(
            "https://documents.gov.gr/tender.pdf",
            resolver=_mixed_resolver,
        )


async def test_test_domains_require_explicit_nonproduction_override():
    await validate_public_http_url("https://documents.example/file", allow_test_hosts=True)

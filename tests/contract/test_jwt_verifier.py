"""JWT verification against a mocked JWKS endpoint (respx) — a real RSA
keypair, a real signed JWT (`PyJWT`), no live OIDC provider needed. Proves
the verifier actually validates signature/issuer/audience/expiry, not just
that it parses a token."""

import json
import time

import httpx
import jwt
import pytest
import respx
from jwt.algorithms import RSAAlgorithm

from packages.auth.config import OidcConfig
from packages.auth.jwt_verifier import JwtVerificationError, JwtVerifier

ISSUER = "https://idp.example.test/"
AUDIENCE = "procintel-api"
JWKS_URL = "https://idp.example.test/.well-known/jwks.json"
DISCOVERY_URL = "https://idp.example.test/.well-known/openid-configuration"
DISCOVERED_JWKS_URL = "https://idp.example.test/protocol/openid-connect/certs"
KID = "test-key-1"


@pytest.fixture(scope="module")
def rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks_body(rsa_keypair):
    _, public_key = rsa_keypair
    jwk_json = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk_json["kid"] = KID
    jwk_json["use"] = "sig"
    jwk_json["alg"] = "RS256"
    return {"keys": [jwk_json]}


def _make_token(rsa_keypair, *, claims_override=None, headers_override=None):
    private_key, _ = rsa_keypair
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "email": "analyst@example.test",
        "role": "ANALYST",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "iat": now,
        "exp": now + 3600,
    }
    if claims_override:
        claims.update(claims_override)
    headers = {"kid": KID}
    if headers_override:
        headers.update(headers_override)
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def _config() -> OidcConfig:
    return OidcConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url=JWKS_URL)


@respx.mock
async def test_verify_a_valid_token_returns_authenticated_user(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(rsa_keypair)

    verifier = JwtVerifier(_config())
    try:
        user = await verifier.verify(token)
    finally:
        await verifier.aclose()

    assert user.subject == "user-123"
    assert user.email == "analyst@example.test"
    assert user.role == "ANALYST"
    assert user.tenant_id == "11111111-1111-1111-1111-111111111111"


@respx.mock
async def test_discovers_provider_jwks_uri_when_no_override_is_configured(rsa_keypair, jwks_body):
    respx.get(DISCOVERY_URL).mock(
        return_value=httpx.Response(
            200,
            json={"issuer": ISSUER, "jwks_uri": DISCOVERED_JWKS_URL},
        )
    )
    respx.get(DISCOVERED_JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    verifier = JwtVerifier(OidcConfig(issuer=ISSUER, audience=AUDIENCE))
    try:
        user = await verifier.verify(_make_token(rsa_keypair))
    finally:
        await verifier.aclose()
    assert user.subject == "user-123"


@respx.mock
async def test_extracts_keycloak_realm_role_when_generic_claim_is_absent(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(
        rsa_keypair,
        claims_override={"role": None, "realm_access": {"roles": ["default-roles-procintel", "ANALYST"]}},
    )
    verifier = JwtVerifier(_config())
    try:
        user = await verifier.verify(token)
    finally:
        await verifier.aclose()
    assert user.role == "ANALYST"


@respx.mock
async def test_selects_highest_recognized_role_from_keycloak_role_list(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(
        rsa_keypair,
        claims_override={"role": ["VIEWER", "SALES", "ANALYST"]},
    )
    verifier = JwtVerifier(_config())
    try:
        user = await verifier.verify(token)
    finally:
        await verifier.aclose()
    assert user.role == "ANALYST"


@respx.mock
async def test_verify_rejects_expired_token(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(rsa_keypair, claims_override={"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200})

    verifier = JwtVerifier(_config())
    try:
        with pytest.raises(JwtVerificationError):
            await verifier.verify(token)
    finally:
        await verifier.aclose()


@respx.mock
async def test_verify_rejects_wrong_audience(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(rsa_keypair, claims_override={"aud": "some-other-api"})

    verifier = JwtVerifier(_config())
    try:
        with pytest.raises(JwtVerificationError):
            await verifier.verify(token)
    finally:
        await verifier.aclose()


@respx.mock
async def test_verify_rejects_wrong_issuer(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(rsa_keypair, claims_override={"iss": "https://not-the-real-idp.example.test/"})

    verifier = JwtVerifier(_config())
    try:
        with pytest.raises(JwtVerificationError):
            await verifier.verify(token)
    finally:
        await verifier.aclose()


@respx.mock
async def test_verify_rejects_a_token_signed_by_a_different_key(jwks_body):
    from cryptography.hazmat.primitives.asymmetric import rsa

    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _make_token((attacker_key, None))

    verifier = JwtVerifier(_config())
    try:
        with pytest.raises(JwtVerificationError):
            await verifier.verify(token)
    finally:
        await verifier.aclose()


@respx.mock
async def test_verify_defaults_to_viewer_role_when_role_claim_missing(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    # a token that never had a role claim at all (not just role=None)
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-456", "iat": now, "exp": now + 3600}
    private_key, _ = rsa_keypair
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})

    verifier = JwtVerifier(_config())
    try:
        user = await verifier.verify(token)
    finally:
        await verifier.aclose()
    assert user.role == "VIEWER"


@respx.mock
async def test_verify_rejects_an_unknown_key_id(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(rsa_keypair, headers_override={"kid": "some-other-key-id"})

    verifier = JwtVerifier(_config())
    try:
        with pytest.raises(JwtVerificationError):
            await verifier.verify(token)
    finally:
        await verifier.aclose()


@respx.mock
async def test_privileged_role_requires_mfa_claim(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(rsa_keypair, claims_override={"role": "OWNER"})
    verifier = JwtVerifier(_config())
    try:
        with pytest.raises(JwtVerificationError, match="MFA is required"):
            await verifier.verify(token)
    finally:
        await verifier.aclose()


@respx.mock
async def test_privileged_role_accepts_verified_mfa_method(rsa_keypair, jwks_body):
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks_body))
    token = _make_token(
        rsa_keypair,
        claims_override={"role": "ADMIN", "amr": ["pwd", "webauthn"]},
    )
    verifier = JwtVerifier(_config())
    try:
        user = await verifier.verify(token)
    finally:
        await verifier.aclose()
    assert user.mfa_verified is True

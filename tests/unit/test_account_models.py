import pytest
from pydantic import ValidationError

from apps.api.routers.account import ApiKeyRequest, InvitationRequest


def test_api_keys_accept_the_enforced_scope_set():
    assert ApiKeyRequest(name="reporting").scopes == ["read"]
    assert ApiKeyRequest(name="automation", scopes=["read", "write"]).scopes == ["read", "write"]
    assert ApiKeyRequest(name="administrator", scopes=["admin"]).scopes == ["admin"]

    with pytest.raises(ValidationError):
        ApiKeyRequest(name="unsafe", scopes=["unknown"])


def test_invitations_validate_email_and_expiry():
    request = InvitationRequest(email="sales@example.test", expires_in_days=30)
    assert request.email == "sales@example.test"

    with pytest.raises(ValidationError):
        InvitationRequest(email="not-an-email")
    with pytest.raises(ValidationError):
        InvitationRequest(email="sales@example.test", expires_in_days=31)

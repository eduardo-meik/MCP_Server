import pytest
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth import AccessToken
from pydantic import ValidationError

from mcp_core import CoreSettings
from mcp_core.identity import resolve_identity

TENANT = "11111111-2222-3333-4444-555555555555"
AZURE = {
    "auth_provider": "azure",
    "azure_tenant_id": TENANT,
    "azure_client_id": "app",
    "azure_client_secret": "secret",
}


def token(claims):
    return AccessToken(token="t", client_id="claude", scopes=[], claims=claims)


def test_production_rejects_unauthenticated_mode():
    with pytest.raises(ValidationError, match="solo está permitido en local"):
        CoreSettings(environment="production", base_url="https://mcp.cliente.cl")


def test_production_requires_secrets():
    with pytest.raises(ValidationError, match="Faltan secretos"):
        CoreSettings(environment="production", base_url="https://mcp.cliente.cl", **AZURE)


def test_azure_rejects_multitenant_authority():
    with pytest.raises(ValidationError, match="GUID del tenant"):
        CoreSettings(**{**AZURE, "azure_tenant_id": "organizations"})


def test_entra_identity_uses_tid_and_oid():
    settings = CoreSettings(**AZURE)
    identity = resolve_identity(
        token(
            {
                "tid": TENANT,
                "oid": "abc",
                "preferred_username": "ana@cliente.cl",
                "roles": ["kyc.judicial"],
            }
        ),
        settings,
    )
    assert identity.subject == f"{TENANT}:abc"
    assert "kyc.judicial" in identity.roles


def test_entra_rejects_other_tenant():
    with pytest.raises(AuthorizationError):
        resolve_identity(token({"tid": "otro", "oid": "abc"}), CoreSettings(**AZURE))


def test_google_enforces_domain_and_verified_email():
    settings = CoreSettings(
        auth_provider="google",
        google_client_id="id",
        google_client_secret="s",
        google_allowed_domains=["cliente.cl"],
    )
    ok = resolve_identity(
        token({"sub": "1", "email": "Ana@Cliente.cl", "email_verified": True}), settings
    )
    assert ok.tenant == "cliente.cl"
    with pytest.raises(AuthorizationError):
        resolve_identity(
            token({"sub": "2", "email": "x@gmail.com", "email_verified": True}), settings
        )
    with pytest.raises(AuthorizationError):
        resolve_identity(token({"sub": "3", "email": "y@cliente.cl"}), settings)

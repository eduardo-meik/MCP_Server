"""SSO: construye el proveedor OAuth de FastMCP según la configuración del cliente.

FastMCP actúa como intermediario OAuth (OAuthProxy):
- hacia Claude/ChatGPT expone OAuth 2.1 con registro dinámico, CIMD, PKCE S256
  y metadata `.well-known`;
- hacia el IdP corporativo (Entra ID o Google Workspace) usa una sola app
  registrada con un único redirect URI fijo (`{base_url}/auth/callback`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_core.config import CoreSettings

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider
    from key_value.aio.protocols import AsyncKeyValue


def build_storage(settings: CoreSettings) -> AsyncKeyValue | None:
    """Storage persistente y cifrado para clientes registrados y tokens upstream.

    En Cloud Run el disco es efímero y hay varias instancias: sin un storage
    compartido, cada deploy obliga a los usuarios a reconectarse.
    """
    if settings.storage_backend == "memory":
        return None  # FastMCP usa memoria/disco local: válido solo en desarrollo.

    from cryptography.fernet import Fernet
    from key_value.aio.stores.firestore import (
        FirestoreStore,
        FirestoreV1CollectionSanitizationStrategy,
        FirestoreV1KeySanitizationStrategy,
    )
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    assert settings.storage_encryption_key is not None
    store = FirestoreStore(
        default_collection=settings.firestore_collection,
        key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
        collection_sanitization_strategy=FirestoreV1CollectionSanitizationStrategy(),
    )
    fernet = Fernet(settings.storage_encryption_key.get_secret_value().encode())
    return FernetEncryptionWrapper(store, fernet=fernet)


def build_auth(settings: CoreSettings) -> AuthProvider | None:
    if settings.auth_provider == "none":
        return None

    common = {
        "base_url": settings.base_url,
        "allowed_client_redirect_uris": settings.allowed_redirect_uris,
        "client_storage": build_storage(settings),
        "jwt_signing_key": (
            settings.jwt_signing_key.get_secret_value() if settings.jwt_signing_key else None
        ),
        "require_authorization_consent": True,
        "fastmcp_access_token_expiry_seconds": settings.access_token_ttl_seconds,
    }

    if settings.auth_provider == "azure":
        from fastmcp.server.auth.providers.azure import AzureProvider

        assert settings.azure_client_secret is not None
        # tenant_id es el GUID del cliente: Entra valida el issuer, así que
        # usuarios de otros tenants no pueden entrar.
        return AzureProvider(
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret.get_secret_value(),
            tenant_id=settings.azure_tenant_id,
            required_scopes=[settings.azure_scope],
            **common,
        )

    from fastmcp.server.auth.providers.google import GoogleProvider

    assert settings.google_client_secret is not None
    return GoogleProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        required_scopes=["openid", "email"],
        # `hd` solo es una pista para la pantalla de Google; la validación real
        # del dominio está en mcp_core.identity.
        extra_authorize_params={"hd": settings.google_allowed_domains[0]},
        **common,
    )

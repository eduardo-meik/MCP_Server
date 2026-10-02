"""Configuración común a todos los MCP generados desde la plantilla.

Todo se lee de variables de entorno (en Cloud Run vienen de Secret Manager),
así cada cliente solo cambia configuración, no código.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Callbacks OAuth de los clientes MCP soportados. Verificar contra la
# documentación vigente de cada asistente antes de cada release.
DEFAULT_ALLOWED_REDIRECT_URIS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "http://localhost:*",
    "http://127.0.0.1:*",
]


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_", env_file=".env", extra="ignore")

    environment: Literal["local", "staging", "production"] = "local"
    server_name: str = "mcp-server"
    base_url: str = "http://localhost:8000"
    port: int = 8000

    # --- SSO ---------------------------------------------------------------
    # "none" solo se permite en local: sirve para probar tools con MCP Inspector.
    auth_provider: Literal["azure", "google", "none"] = "none"

    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: SecretStr | None = None
    # Nombre del scope expuesto en "Expose an API" de la app de Entra ID.
    azure_scope: str = "mcp.access"

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Dominios de Google Workspace autorizados (ej. "cliente.cl").
    google_allowed_domains: list[str] = Field(default_factory=list)

    allowed_redirect_uris: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_REDIRECT_URIS)
    )
    # Firma de los tokens que emite el proxy OAuth. Fija entre despliegues para
    # que los usuarios no tengan que reconectarse.
    jwt_signing_key: SecretStr | None = None
    # Cifrado de los tokens upstream guardados en el storage.
    storage_encryption_key: SecretStr | None = None
    storage_backend: Literal["memory", "firestore"] = "memory"
    firestore_collection: str = "mcp-oauth"
    access_token_ttl_seconds: int = 900

    # --- Autorización -------------------------------------------------------
    # Emails con roles asignados manualmente (útil con Google, que no emite roles).
    role_assignments: dict[str, list[str]] = Field(default_factory=dict)

    # --- Operación ----------------------------------------------------------
    rate_limit_per_second: float = 2.0
    rate_limit_burst: int = 10
    # Sal para seudonimizar datos personales en el log de auditoría.
    audit_hash_salt: SecretStr = SecretStr("cambiar-en-produccion")

    @model_validator(mode="after")
    def _validate(self) -> CoreSettings:
        if self.environment != "local":
            if self.auth_provider == "none":
                raise ValueError("MCP_AUTH_PROVIDER=none solo está permitido en local")
            if not self.base_url.startswith("https://"):
                raise ValueError("MCP_BASE_URL debe ser https fuera de local")
            missing = [
                name
                for name in ("jwt_signing_key", "storage_encryption_key")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"Faltan secretos obligatorios: {', '.join(missing)}")
            if self.audit_hash_salt.get_secret_value() == "cambiar-en-produccion":
                raise ValueError("MCP_AUDIT_HASH_SALT debe definirse fuera de local")
        if self.auth_provider == "azure" and not (
            self.azure_tenant_id and self.azure_client_id and self.azure_client_secret
        ):
            raise ValueError("Azure requiere MCP_AZURE_TENANT_ID, _CLIENT_ID y _CLIENT_SECRET")
        if self.auth_provider == "azure" and self.azure_tenant_id in (
            "common",
            "organizations",
            "consumers",
        ):
            raise ValueError("Usa el GUID del tenant del cliente, no un tenant multi-organización")
        if self.auth_provider == "google":
            if not (self.google_client_id and self.google_client_secret):
                raise ValueError("Google requiere MCP_GOOGLE_CLIENT_ID y _CLIENT_SECRET")
            if not self.google_allowed_domains:
                raise ValueError("Google requiere MCP_GOOGLE_ALLOWED_DOMAINS")
        return self

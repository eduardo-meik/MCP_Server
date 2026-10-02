"""Identidad normalizada del usuario, independiente del IdP.

Entra ID y Google entregan claims distintos; las tools y la auditoría solo
ven `Identity`. Aquí también se aplica la validación de tenant o dominio,
como defensa en profundidad además de la que hace el propio IdP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastmcp.exceptions import AuthorizationError

from mcp_core.config import CoreSettings

if TYPE_CHECKING:
    from fastmcp.server.auth import AccessToken, AuthCheck, AuthContext

LOCAL_SUBJECT = "local-dev"


@dataclass(frozen=True)
class Identity:
    subject: str  # identificador estable: "tid:oid" en Entra, "sub" en Google
    email: str | None
    tenant: str | None  # GUID de Entra o dominio de Google Workspace
    client_id: str | None  # cliente MCP (Claude, ChatGPT...) que hace la llamada
    roles: frozenset[str] = field(default_factory=frozenset)


def resolve_identity(token: AccessToken | None, settings: CoreSettings) -> Identity:
    if settings.auth_provider == "none":
        return Identity(
            subject=LOCAL_SUBJECT,
            email=None,
            tenant="local",
            client_id=None,
            roles=frozenset(settings.role_assignments.get(LOCAL_SUBJECT, [])),
        )
    if token is None:
        raise AuthorizationError("Se requiere autenticación")

    claims = token.claims or {}
    if settings.auth_provider == "azure":
        return _from_entra(token, claims, settings)
    return _from_google(token, claims, settings)


def _from_entra(token: AccessToken, claims: dict, settings: CoreSettings) -> Identity:
    tid, oid = claims.get("tid"), claims.get("oid")
    # Nunca identificar por email en Entra: puede ser editable o no verificado.
    if not tid or not oid:
        raise AuthorizationError("El token de Entra ID no trae tid/oid")
    if tid.lower() != (settings.azure_tenant_id or "").lower():
        raise AuthorizationError("El usuario no pertenece a la organización autorizada")
    email = claims.get("preferred_username") or claims.get("upn") or claims.get("email")
    roles = set(_as_list(claims.get("roles")))
    roles |= set(settings.role_assignments.get((email or "").lower(), []))
    return Identity(
        subject=f"{tid}:{oid}",
        email=email,
        tenant=tid,
        client_id=token.client_id,
        roles=frozenset(roles),
    )


def _from_google(token: AccessToken, claims: dict, settings: CoreSettings) -> Identity:
    email = (claims.get("email") or "").lower()
    if not email or not claims.get("email_verified"):
        raise AuthorizationError("La cuenta de Google no tiene un email verificado")
    domain = email.rsplit("@", 1)[-1]
    allowed = {d.lower() for d in settings.google_allowed_domains}
    if domain not in allowed:
        raise AuthorizationError("El dominio del usuario no está autorizado")
    sub = claims.get("sub") or token.subject
    if not sub:
        raise AuthorizationError("El token de Google no trae sub")
    return Identity(
        subject=str(sub),
        email=email,
        tenant=domain,
        client_id=token.client_id,
        roles=frozenset(settings.role_assignments.get(email, [])),
    )


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]  # type: ignore[union-attr]


def require_role(role: str, settings: CoreSettings) -> AuthCheck:
    """Auth check de FastMCP: la tool solo se lista y ejecuta con el rol indicado.

    En Entra el rol viene de los App Roles de la aplicación; con Google se
    asigna por email en MCP_ROLE_ASSIGNMENTS.
    """

    def check(ctx: AuthContext) -> bool:
        try:
            identity = resolve_identity(ctx.token, settings)
        except AuthorizationError:
            return False
        return role in identity.roles

    return check

"""Núcleo común de los MCP enterprise de la plantilla (SSO, gobierno, auditoría)."""

from mcp_core.app import create_server
from mcp_core.config import CoreSettings
from mcp_core.governance import current_identity
from mcp_core.identity import Identity, require_role

__all__ = ["CoreSettings", "Identity", "create_server", "current_identity", "require_role"]

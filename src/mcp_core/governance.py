"""Middleware de gobierno: identidad, auditoría y límite de uso por usuario.

Cada llamada a una tool deja un registro JSON en stdout, que Cloud Run envía a
Cloud Logging (con un sink a un bucket bloqueado queda como auditoría inmutable).
Los argumentos se seudonimizan: el log nunca guarda RUT, teléfonos ni nombres.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sys
import time
import uuid
from typing import Any

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

from mcp_core.config import CoreSettings
from mcp_core.identity import Identity, resolve_identity

audit_logger = logging.getLogger("mcp.audit")


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.handlers = [handler]
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False


def pseudonymize(value: Any, salt: str) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hmac.new(salt.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]


def current_identity(settings: CoreSettings) -> Identity:
    return resolve_identity(get_access_token(), settings)


class GovernanceMiddleware(Middleware):
    def __init__(self, settings: CoreSettings) -> None:
        self.settings = settings
        self._salt = settings.audit_hash_salt.get_secret_value()

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool = context.message.name
        arguments = context.message.arguments or {}
        event: dict[str, Any] = {
            "severity": "INFO",
            "log_type": "mcp-audit",
            "event_id": str(uuid.uuid4()),
            "server": self.settings.server_name,
            "tool": tool,
            # Un hash por argumento permite buscar "todas las consultas a este RUT"
            # recalculando el hash, sin guardar el dato en claro.
            "args": {k: pseudonymize(v, self._salt) for k, v in arguments.items()},
        }
        started = time.perf_counter()
        try:
            identity = current_identity(self.settings)
            event.update(
                subject=identity.subject,
                tenant=identity.tenant,
                mcp_client_id=identity.client_id,
            )
            result = await call_next(context)
            event["outcome"] = "error" if getattr(result, "is_error", False) else "ok"
            return result
        except AuthorizationError:
            event.update(outcome="denied", severity="WARNING")
            raise
        except Exception as exc:
            event.update(outcome="exception", severity="ERROR", error=type(exc).__name__)
            raise
        finally:
            event["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
            audit_logger.info(json.dumps(event, ensure_ascii=False))


def build_rate_limiter(settings: CoreSettings) -> RateLimitingMiddleware:
    """Límite por usuario (no por IP: muchos usuarios salen por la misma IP del asistente)."""

    def by_subject(_: MiddlewareContext) -> str:
        try:
            return current_identity(settings).subject
        except AuthorizationError:
            return "anonymous"

    return RateLimitingMiddleware(
        max_requests_per_second=settings.rate_limit_per_second,
        burst_capacity=settings.rate_limit_burst,
        get_client_id=by_subject,
    )

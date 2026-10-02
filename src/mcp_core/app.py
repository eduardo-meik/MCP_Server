"""Fábrica del servidor: todo MCP de la plantilla parte de `create_server`."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_core.auth import build_auth
from mcp_core.config import CoreSettings
from mcp_core.governance import GovernanceMiddleware, build_rate_limiter, configure_logging


def create_server(settings: CoreSettings, *, instructions: str) -> FastMCP:
    configure_logging()
    mcp = FastMCP(
        name=settings.server_name,
        instructions=instructions,
        auth=build_auth(settings),
    )
    mcp.add_middleware(GovernanceMiddleware(settings))
    mcp.add_middleware(build_rate_limiter(settings))

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp

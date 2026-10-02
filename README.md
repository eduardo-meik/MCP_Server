# MCP Server enterprise: plantilla + piloto Boostr KYC

Plantilla para construir MCP a medida de cada cliente, conectables desde Claude y ChatGPT
con login SSO (Entra ID o Google Workspace), desplegados en Cloud Run.

- **`mcp_core`**: SSO, identidad, roles, auditoría y rate limit. Es común a todos los clientes.
- **`boostr_kyc`**: MCP piloto para evaluar prospectos y prevenir fraude con la
  [API de Boostr](https://docs.boostr.cl/reference/welcome).

El plan completo está en [PLAN.md](PLAN.md).

## Desarrollo local

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env            # agrega BOOSTR_API_KEY
python -m boostr_kyc            # http://localhost:8000/mcp, sin login (solo en local)
pytest
```

Para probar las tools: `npx @modelcontextprotocol/inspector`, transporte *Streamable HTTP*,
URL `http://localhost:8000/mcp`.

Para probar el SSO en local, configura `MCP_AUTH_PROVIDER=azure` o `google` y registra
`http://localhost:8000/auth/callback` como redirect URI en el IdP.

## Despliegue

Ver [docs/despliegue-gcp.md](docs/despliegue-gcp.md). Guía para usuarios finales:
[docs/conexion.md](docs/conexion.md).

## Crear un MCP para otro cliente

Hasta que exista la plantilla Copier (fase 3 del plan):

1. Copia `src/boostr_kyc` como `src/<nuevo>` y reemplaza las tools.
2. Arma el servidor con `mcp_core.create_server(settings, instructions=...)`.
3. Para restringir una tool a un rol, usa `auth=require_role("<rol>", settings)`.
4. Para leer el usuario que llama, usa `current_identity(settings)`.

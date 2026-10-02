# Plan: plantilla de MCP Server enterprise grade con login SSO

> Objetivo: una plantilla reutilizable para publicar un MCP Server remoto con la misma
> experiencia que ofrece Global66 Empresas: **una sola URL**, sin API keys, login con el
> usuario corporativo (SSO), consentimiento de permisos por scopes y operaciones sensibles
> que **nunca se ejecutan sin confirmación humana fuera del asistente**.

---

## 1. Qué hay que replicar (requisitos extraídos del caso Global66)

| # | Requisito observado | Cómo lo cubre la plantilla |
|---|---|---|
| R1 | Una sola URL (`https://mcp.<empresa>.com/mcp`) | Endpoint único `/mcp` con transporte **Streamable HTTP** |
| R2 | Sin stdio ni SSE | Solo Streamable HTTP (POST + GET opcional) |
| R3 | Sin API keys ni tokens estáticos | Solo **OAuth 2.1**; cualquier request sin Bearer válido → `401` |
| R4 | Login con el usuario de siempre | Authorization Server propio que **federa** al IdP corporativo (OIDC o SAML) |
| R5 | El usuario elige qué permisos da | Pantalla de **consentimiento por scopes** |
| R6 | Dynamic Client Registration | **RFC 7591** (DCR) + **Client ID Metadata Documents** (spec MCP 2025-11-25) |
| R7 | PKCE obligatorio, solo `S256` | El AS rechaza `plain` y requests sin `code_challenge` |
| R8 | Descubrimiento por `.well-known` | **RFC 9728** (Protected Resource Metadata) + **RFC 8414** (AS Metadata) / OIDC Discovery |
| R9 | Funciona en Claude, ChatGPT, Claude Code, Codex, Cursor, VS Code, Windsurf, Cline | Matriz de compatibilidad + pruebas E2E por cliente |
| R10 | Planes Team/Enterprise: un owner lo agrega, cada persona hace *Connect* | Tokens por usuario (no por organización), aislamiento multi-tenant |
| R11 | Nada que mueva dinero sale sin confirmación | Patrón **preparar → confirmar fuera de banda** (app/web/push) |
| R12 | Herramientas de consulta (saldos, movimientos, cotizar) | Catálogo de tools con scopes y anotaciones `readOnlyHint`/`destructiveHint` |

---

## 2. Arquitectura

```
 ┌───────────────┐   1. POST /mcp (sin token)        ┌──────────────────────────┐
 │ Cliente MCP   │ ─────────────────────────────────▶│  MCP Resource Server     │
 │ (Claude,      │ ◀── 401 + WWW-Authenticate:       │  mcp.empresa.com/mcp     │
 │  ChatGPT,     │     resource_metadata=...         │  - valida JWT (aud/iss)  │
 │  Cursor...)   │                                   │  - scopes por tool       │
 │               │   2. GET /.well-known/            │  - tenant isolation      │
 │               │      oauth-protected-resource     │  - audit log             │
 │               │ ─────────────────────────────────▶└───────────┬──────────────┘
 │               │                                               │ API interna
 │               │   3. discovery + DCR/CIMD + /authorize        ▼ (mTLS / token exchange)
 │               │ ─────────────────────────────────▶┌──────────────────────────┐
 │               │                                   │ Authorization Server     │
 │               │ ◀── 6. code → /token (PKCE S256)  │ auth.empresa.com         │
 │               │     access_token (JWT, aud=MCP)   │ - DCR / CIMD             │
 └───────────────┘     refresh_token (rotativo)      │ - consentimiento scopes  │
                                                     │ - revocación             │
                         4. redirect SSO ───────────▶└───────────┬──────────────┘
                                                                 │ OIDC / SAML
                                                                 ▼
                                                     ┌──────────────────────────┐
                                                     │ IdP corporativo          │
                                                     │ Entra ID / Okta / Google │
                                                     │ Workspace / Keycloak     │
                                                     │ (MFA, políticas)         │
                                                     └──────────────────────────┘
```

### Decisión clave: Authorization Server "broker"

Los IdP corporativos (Entra ID, Okta, Google) **no aceptan registro dinámico de clientes
arbitrarios** ni emiten tokens con `aud` del MCP. Por eso la plantilla incluye un AS propio que:

1. Habla OAuth 2.1 "MCP-compliant" hacia los clientes (DCR, CIMD, PKCE S256, RFC 8707).
2. Delega la **autenticación** al IdP corporativo vía OIDC (o SAML) — eso es el SSO.
3. Emite **sus propios** tokens, con `aud = https://mcp.empresa.com/mcp`, scopes consentidos,
   `sub`, `org_id`, `roles`. Nunca reenvía el token del IdP al cliente ni a APIs aguas abajo
   (prohibido por la spec: *token passthrough*).

Opciones de implementación (la plantilla trae la A y documenta las demás):

| Opción | Pros | Contras |
|---|---|---|
| **A. AS embebido con `oidc-provider` (panva)** | Control total, sin costo por MAU, certificado OpenID | Hay que operarlo (claves, DB, rotación) |
| B. Keycloak (identity brokering) | Maduro, DCR nativo, SAML/OIDC, admin UI | Operación pesada (JVM), tuning de DCR |
| C. SaaS (WorkOS AuthKit, Auth0, Stytch, Descope) | Rápido, soporte MCP listo | Costo y lock-in, datos fuera de la región |

La capa de verificación del Resource Server solo depende de `issuer` + JWKS, así que se puede
cambiar de A a B/C sin tocar las tools.

---

## 3. Stack propuesto

- **Lenguaje**: TypeScript (Node 22 LTS).
- **MCP**: `@modelcontextprotocol/sdk` oficial, `StreamableHTTPServerTransport`, modo **stateless**
  (sin sesión en memoria → escalado horizontal sin sticky sessions).
- **HTTP**: Express o Hono.
- **AS**: `oidc-provider` + adapter Postgres/Redis; `openid-client` para federar al IdP.
- **Validación**: `zod` para inputs y `outputSchema` de cada tool.
- **JWT**: `jose` (verificación con JWKS cacheado, ES256/EdDSA).
- **Datos**: Postgres (clientes, grants, consentimientos, auditoría), Redis (rate limit, códigos, revocación).
- **Observabilidad**: OpenTelemetry (trazas + métricas), logs JSON (pino) con redacción de PII.
- **Infra**: Docker, Terraform, despliegue en Cloud Run / ECS Fargate / Kubernetes detrás de WAF.
- **Secretos**: KMS/Secret Manager; claves de firma en KMS o HSM, nunca en el repo.

---

## 4. Estructura del repositorio

```
mcp-enterprise-template/
├── apps/
│   ├── mcp-server/                # Resource Server
│   │   ├── src/
│   │   │   ├── index.ts           # bootstrap HTTP + /mcp + well-known
│   │   │   ├── auth/
│   │   │   │   ├── verifyToken.ts     # JWT: iss, aud, exp, scopes, revocación
│   │   │   │   ├── wwwAuthenticate.ts # 401/403 con resource_metadata y scope
│   │   │   │   └── protectedResource.ts # RFC 9728
│   │   │   ├── tools/
│   │   │   │   ├── registry.ts    # registro + scope requerido + anotaciones
│   │   │   │   ├── accounts.ts    # get_my_accounts            (read)
│   │   │   │   ├── movements.ts   # query_movements            (read)
│   │   │   │   ├── quotes.ts      # create_quote               (read, sin efecto)
│   │   │   │   └── transfers.ts   # prepare_transfer           (write → pendiente)
│   │   │   ├── context.ts         # RequestContext: user, org, scopes, traceId
│   │   │   ├── audit.ts
│   │   │   └── backend/           # cliente a la API core (token exchange / mTLS)
│   │   └── test/
│   └── auth-server/               # Authorization Server (broker SSO)
│       ├── src/
│       │   ├── provider.ts        # oidc-provider: DCR, PKCE S256, resource indicators
│       │   ├── cimd.ts            # Client ID Metadata Documents
│       │   ├── upstream/
│       │   │   ├── oidc.ts        # Entra ID / Okta / Google / Keycloak
│       │   │   └── saml.ts        # opcional
│       │   ├── interactions/      # login, selección de org, consentimiento
│       │   ├── views/             # UI de consentimiento (marca blanca)
│       │   └── policies/          # allowlist de redirect URIs, dominios por tenant
│       └── test/
├── packages/
│   ├── scopes/                    # catálogo único de scopes (compartido AS ↔ RS)
│   └── shared/                    # tipos, logger, otel
├── infra/                         # Terraform, docker-compose (local con Keycloak de IdP)
├── docs/                          # guías de conexión por cliente (como la de Global66)
└── .github/workflows/             # CI: lint, tests, conformance, SAST, SBOM
```

---

## 5. Flujo OAuth detallado (lo que debe cumplir cada endpoint)

1. **`POST /mcp` sin token** → `401` con
   `WWW-Authenticate: Bearer resource_metadata="https://mcp.empresa.com/.well-known/oauth-protected-resource", scope="accounts:read"`.
2. **`GET /.well-known/oauth-protected-resource`** (también la variante con sufijo `/mcp`):
   `resource`, `authorization_servers`, `scopes_supported`, `bearer_methods_supported: ["header"]`.
3. **`GET https://auth.empresa.com/.well-known/oauth-authorization-server`** y
   `/.well-known/openid-configuration`: `registration_endpoint`,
   `code_challenge_methods_supported: ["S256"]`, `client_id_metadata_document_supported: true`,
   `grant_types_supported: ["authorization_code","refresh_token"]`,
   `token_endpoint_auth_methods_supported: ["none","private_key_jwt"]`.
4. **Registro del cliente**:
   - CIMD: `client_id` es una URL HTTPS; el AS descarga y valida el documento (caché, SSRF-safe).
   - DCR (`POST /register`): clientes públicos, `token_endpoint_auth_method: none`.
     Política de `redirect_uris`: HTTPS en allowlist o `http://127.0.0.1|localhost:<puerto>` (loopback para CLIs).
     Rate limit por IP y expiración de clientes sin uso.
5. **`/authorize`**: exige `code_challenge` + `code_challenge_method=S256`, `state`, y
   `resource=https://mcp.empresa.com/mcp` (RFC 8707). Rechaza cualquier otro método.
6. **SSO**: el AS redirige al IdP del tenant (por dominio de email o *home realm discovery*),
   recibe `id_token`, mapea `sub`/`email`/grupos → usuario y roles internos. MFA lo impone el IdP.
7. **Consentimiento**: pantalla con nombre del cliente (marcado "no verificado" si viene de DCR),
   lista de scopes con descripción en lenguaje claro, casillas para quitar scopes opcionales.
8. **`/token`**: verifica PKCE, emite
   - access token JWT de **5–15 min**, `aud` = recurso MCP, `scope`, `org_id`, `sub`, `jti`;
   - refresh token **rotativo** con detección de reuso (revoca toda la familia).
9. **Revocación** (`/revoke`, RFC 7009) + portal "Aplicaciones conectadas" para que el usuario
   o el admin del tenant desconecte un asistente.

---

## 6. Modelo de permisos

### Scopes (ejemplo fintech, reemplazable)

| Scope | Permite | Riesgo |
|---|---|---|
| `accounts:read` | Ver cuentas y saldos | Bajo |
| `movements:read` | Consultar movimientos y detalle | Bajo |
| `beneficiaries:read` | Listar beneficiarios | Medio (PII) |
| `quotes:write` | Crear cotizaciones (sin efecto contable) | Bajo |
| `transfers:prepare` | Dejar un envío **pendiente de confirmación** | Alto, mitigado |

**No existe** un scope que ejecute transferencias: la ejecución solo ocurre en el canal
confirmado (app móvil con biometría/2FA).

### Autorización en 3 capas

1. **Scope** del token (lo que el usuario consintió).
2. **Rol** del usuario en la organización (lo que el usuario puede hacer de por sí).
3. **Políticas** del tenant (montos máximos, horario, beneficiarios permitidos).

Permiso efectivo = intersección de las tres. Falta de scope → `403` con
`WWW-Authenticate: Bearer error="insufficient_scope", scope="..."` (step-up de scopes).

---

## 7. Diseño de tools

Reglas para todas las tools:

- `inputSchema` y `outputSchema` estrictos (zod), descripciones claras en el idioma del usuario.
- Anotaciones: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
- Respuesta con `structuredContent` + texto resumido.
- Paginación por cursor y límites de tamaño (evita reventar el contexto del modelo).
- Errores de negocio como `isError: true` con mensaje accionable, nunca stack traces.
- `tenant_id` **siempre** del token, nunca de un argumento.

### Patrón "preparar → confirmar fuera de banda" (operaciones que mueven dinero)

```
prepare_transfer(beneficiary_id, amount, currency, quote_id, idempotency_key)
  → crea orden en estado PENDING_CONFIRMATION (expira en N min)
  → push / notificación a la app del usuario
  → devuelve { order_id, status: "pending_confirmation", confirm_url }
get_transfer_status(order_id) → pending | confirmed | rejected | expired
```

- `idempotency_key` obligatorio para evitar duplicados por reintentos del modelo.
- Confirmación con 2FA/biometría en el canal propio; el asistente nunca recibe el OTP.
- Opcional: MCP **elicitation** para pedir confirmación en el cliente como capa adicional,
  pero **nunca** como único control.

---

## 8. Seguridad (checklist enterprise)

- [ ] Validación de `Origin` en `/mcp` (protección DNS rebinding) y CORS solo donde aplica.
- [ ] TLS 1.2+ en todos lados, HSTS.
- [ ] Verificación JWT: `iss`, `aud` exacto, `exp`/`nbf`, `alg` en allowlist, JWKS con caché y rotación.
- [ ] Sin *token passthrough*: hacia el backend se usa token exchange (RFC 8693) o mTLS con identidad de servicio + `sub` firmado.
- [ ] Rotación de claves de firma (KMS) con `kid` y periodo de solapamiento.
- [ ] Rate limiting por usuario, por cliente (`client_id`) y por tenant; cuotas en tools caras.
- [ ] DCR protegido: rate limit, validación de `redirect_uris`, limpieza de clientes huérfanos.
- [ ] CIMD: fetch con protección SSRF (bloqueo IPs privadas, timeout, tamaño máximo).
- [ ] Mitigación de *prompt injection*: datos de terceros (glosas, nombres de beneficiarios) marcados como datos; ninguna tool sensible se dispara solo por contenido devuelto.
- [ ] Redacción de PII en logs; datos sensibles (números de cuenta) enmascarados en respuestas cuando no son necesarios.
- [ ] Audit log inmutable (append-only / WORM): quién, qué tool, qué argumentos (hash), resultado, `client_id`, IP, `traceId`.
- [ ] WAF + protección DDoS delante del RS y del AS.
- [ ] SAST, escaneo de dependencias, SBOM y firma de imágenes en CI.
- [ ] Pentest externo antes de GA; threat model STRIDE documentado.
- [ ] Mapeo a ISO 27001 / SOC 2 (control de acceso, logging, gestión de cambios) y, si aplica, regulación local (CMF en Chile, Ley 21.521 Fintec).

---

## 9. Multi-tenant y administración

- Tenant resuelto por el IdP (dominio/conexión SSO) y fijado como claim `org_id`.
- Consola admin por tenant: configurar conexión SSO (metadata OIDC/SAML), habilitar/deshabilitar
  scopes, ver clientes conectados, revocar sesiones, exportar auditoría.
- SCIM (opcional, fase 4) para desprovisionar usuarios → revocación inmediata de grants.
- Política "solo clientes aprobados": allowlist de `client_id`/dominios CIMD por tenant.

---

## 10. Observabilidad y operación

- Métricas: tasa de 401/403, latencia p95 por tool, errores por cliente MCP, logins SSO fallidos,
  reuso de refresh tokens detectado.
- Trazas OTel de punta a punta: cliente → RS → backend, con `traceId` en el audit log.
- SLO inicial: 99,9 % disponibilidad de `/mcp`, p95 < 800 ms en tools de lectura.
- Runbooks: rotación de claves, revocación masiva, IdP caído, incidente de seguridad.
- Health checks `/healthz` y `/readyz` (sin auth, sin datos).

---

## 11. Compatibilidad con clientes (pruebas E2E)

| Cliente | Configuración | Particularidad a probar |
|---|---|---|
| Claude (web/desktop) | Custom connector con la URL | Callback `https://claude.ai/api/mcp/auth_callback` (y `claude.com`) en allowlist |
| Claude Team/Enterprise | Owner agrega en Organization settings | Un conector, tokens por usuario |
| ChatGPT | Developer mode → conector | DCR + callback de OpenAI en allowlist |
| Claude Code | `claude mcp add --transport http <name> <url>` | Redirect loopback `127.0.0.1` |
| Codex | `codex mcp add` + `codex mcp login` | No inicia OAuth solo |
| Cursor | `~/.cursor/mcp.json` → `url` | Flujo OAuth en su UI |
| VS Code | `mcp.json` → `type: http` | Inicia OAuth al primer uso |
| Windsurf | `serverUrl` | — |
| Cline | `type: streamableHttp` | — |
| MCP Inspector | herramienta de desarrollo | Validación de discovery y tools |

La plantilla incluye `docs/conexion.md` generado con el mismo formato que la guía de Global66
(sin código / para desarrolladores / otros clientes / probar la conexión).

---

## 12. Fases y entregables

| Fase | Duración estimada | Entregables | Criterio de salida |
|---|---|---|---|
| **0. Diseño** | 1 semana | Threat model, catálogo de scopes, ADRs (AS propio vs SaaS, stateless) | ADRs aprobados |
| **1. Esqueleto RS** | 1 semana | `/mcp` Streamable HTTP stateless, 1 tool de lectura, `401` + RFC 9728, docker-compose | MCP Inspector lista tools con token de prueba |
| **2. Authorization Server** | 2 semanas | `oidc-provider` con DCR, CIMD, PKCE S256, RFC 8707, refresh rotativo, revocación | Suite de conformance OAuth pasa; rechazo de `plain` y sin PKCE verificado |
| **3. SSO** | 1–2 semanas | Federación OIDC (Entra ID, Google, Okta, Keycloak local); SAML opcional; consentimiento | Login SSO + MFA del IdP end-to-end desde Claude Code |
| **4. Tools y patrón de confirmación** | 2 semanas | Tools de lectura, cotización, `prepare_transfer` + confirmación en app, idempotencia | Ninguna operación de escritura se ejecuta sin confirmación (tests negativos) |
| **5. Hardening** | 2 semanas | Rate limit, WAF, audit log WORM, OTel, redacción PII, rotación de claves, SAST/SBOM | Pentest sin hallazgos críticos/altos abiertos |
| **6. Multi-tenant y admin** | 2 semanas | Consola admin, allowlist de clientes, portal "aplicaciones conectadas", SCIM opcional | Revocación efectiva < 1 min |
| **7. Certificación de clientes y docs** | 1 semana | E2E con los 9 clientes de la tabla, guía pública de conexión | Matriz 100 % verde |
| **8. GA** | — | Beta cerrada con 3–5 empresas, luego GA | SLO cumplido 30 días en beta |

Total aproximado: **12–14 semanas** con un equipo de 2–3 ingenieros + apoyo de seguridad.

---

## 13. Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Cambios en la spec MCP de autorización (DCR → CIMD) | Soportar ambos; tests de conformance en CI; seguir releases de la spec |
| Clientes con implementaciones OAuth parciales | Matriz E2E por cliente; mensajes de error claros en `WWW-Authenticate` |
| Abuso de DCR (registro masivo, phishing de consentimiento) | Rate limit, etiqueta "cliente no verificado", allowlist por tenant |
| Prompt injection que induce operaciones | Confirmación fuera de banda obligatoria; scopes mínimos; sin ejecución directa |
| Fuga de datos entre tenants | `org_id` solo desde token; tests de aislamiento; RLS en Postgres |
| Operación del AS propio | Opción de migrar a Keycloak/SaaS sin tocar el RS (solo `issuer` + JWKS) |

---

## 14. Próximos pasos inmediatos

1. Confirmar decisiones abiertas:
   - AS embebido (`oidc-provider`) vs Keycloak vs SaaS.
   - IdPs a soportar en el MVP (¿Entra ID + Google Workspace?).
   - Dominio de negocio de las tools de ejemplo (fintech como Global66 u otro).
2. Inicializar el monorepo (fase 1) con docker-compose: RS + AS + Keycloak como IdP de prueba + Postgres + Redis.
3. Escribir los ADRs de la fase 0 en `docs/adr/`.

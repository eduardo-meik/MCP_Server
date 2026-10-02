# Plan: plantilla de MCP enterprise con SSO (Python + FastMCP + Google Cloud)

> **Objetivo:** construir MCP a medida para cada cliente, conectables desde Claude y
> ChatGPT con una sola URL, con login SSO corporativo (Entra ID o Google Workspace),
> grado enterprise, fáciles de mantener y de bajo costo.
>
> **Piloto:** `boostr_kyc`, un MCP para evaluar prospectos y prevenir fraude con la
> API de [Boostr](https://docs.boostr.cl/reference/welcome). Ya está implementado en este repo.

---

## 1. Decisiones de arquitectura

| Decisión | Elección | Motivo |
|---|---|---|
| Lenguaje / framework | **Python 3.12 + FastMCP 4** (versión fijada) | Stack del equipo; FastMCP trae OAuth, middleware y auth por tool |
| Login | **`OAuthProxy` de FastMCP** (`AzureProvider` / `GoogleProvider`) directo al IdP del cliente | Sin Keycloak ni Identity Platform que operar |
| Modelo de despliegue | **Un servicio Cloud Run por cliente** (single-tenant) | Aislamiento real, un solo IdP por servicio, tools a medida |
| Storage OAuth | **Firestore** cifrado con Fernet | Free tier, serverless, sin instancias (más barato que Redis/Memorystore) |
| Secretos | **Secret Manager** | Inyectados como variables de entorno en Cloud Run |
| Auditoría | JSON a stdout → **Cloud Logging** → bucket dedicado con retención | Sin infraestructura extra |
| Transporte | Streamable HTTP **sin estado** (`stateless_http=True`) | Cualquier instancia atiende cualquier request |
| Reutilización | **`mcp_core`** (paquete común) + plantilla **Copier** | Las mejoras de seguridad llegan a todos los clientes |

### Descartado

| Opción | Por qué no |
|---|---|
| Keycloak | Operar una app Java + Cloud SQL rompe "bajo costo" y "fácil de mantener" |
| Identity Platform | Sobra con un IdP por cliente. Solo si un cliente necesita varios IdP en un mismo MCP |
| Cloud IAM / IAP | No son servidores OAuth para clientes MCP (sin registro dinámico ni `.well-known`) |
| Multi-tenant compartido | Más riesgo de fuga entre clientes y peor ajuste a tools a medida |
| Redis / Memorystore | Costo fijo mensual; Firestore cubre el caso dentro del free tier |

---

## 2. Arquitectura

```
Claude / ChatGPT / Claude Code
        │  OAuth 2.1: registro dinámico (DCR) o CIMD, PKCE S256, .well-known
        ▼
┌───────────────────────── Cloud Run: mcp-<cliente> ─────────────────────────┐
│ FastMCP                                                                    │
│  ├─ OAuthProxy ── OIDC ──▶ Entra ID / Google Workspace del cliente         │
│  │    (un app registration, un redirect URI: {base_url}/auth/callback)     │
│  ├─ GovernanceMiddleware: identidad, tenant, auditoría seudonimizada       │
│  ├─ RateLimitingMiddleware: límite por usuario                             │
│  └─ tools del cliente (con scopes/roles por tool)                          │
└───────┬──────────────────────┬─────────────────────────┬───────────────────┘
        │                      │                         │
   Secret Manager         Firestore                Cloud Logging
   (claves, API keys)     (clientes OAuth,         (auditoría → bucket
                           tokens cifrados)          con retención)
        │
        ▼
   API del negocio (en el piloto: api.boostr.cl con X-API-KEY)
```

Verificado en el piloto: `POST /mcp` sin token responde `401` con
`WWW-Authenticate: Bearer resource_metadata=…`, la metadata publica
`registration_endpoint`, `code_challenge_methods_supported: ["S256"]` y
`client_id_metadata_document_supported: true`.

---

## 3. Seguridad enterprise incluida en `mcp_core`

- [x] **Sin modo inseguro en producción:** `MCP_AUTH_PROVIDER=none` solo arranca con `MCP_ENVIRONMENT=local`; fuera de local se exige https y todos los secretos.
- [x] **Entra ID:** tenant fijado por GUID (se rechazan `common`/`organizations`); identidad por `tid` + `oid`, **nunca por email** (evita "nOAuth"); se revalida el `tid` en cada llamada.
- [x] **Google Workspace:** email verificado y dominio en lista blanca.
- [x] **Roles por tool:** App Roles de Entra (`roles`) o asignación por email (`MCP_ROLE_ASSIGNMENTS`). Una tool sin rol ni siquiera aparece en la lista.
- [x] **Redirect URIs permitidos:** solo Claude, ChatGPT y loopback (CLIs).
- [x] **Tokens:** access token de 15 min emitido por FastMCP, firma con clave fija, tokens upstream cifrados en Firestore.
- [x] **Auditoría:** cada llamada registra usuario, tenant, cliente MCP, tool, resultado y duración. Los argumentos se guardan como HMAC: se puede buscar "todas las consultas a este RUT" sin almacenar el RUT.
- [x] **Rate limit por usuario** (no por IP: los asistentes comparten IPs).
- [x] **Contenedor no root**, health check `/healthz`.
- [ ] Cloud Armor delante del servicio (opcional; requiere load balancer, ~US$18/mes).
- [ ] SCIM para desprovisionamiento inmediato (fase 4).
- [ ] Pentest externo antes del primer cliente en producción.

---

## 4. Piloto `boostr_kyc`: evaluación de prospectos y fraude

### Tools

| Tool | Fuente | Costo | Acceso |
|---|---|---|---|
| `validar_rut` | Local (módulo 11) | Gratis | Todos |
| `consultar_nombre_sii` | SII | Pagada | Todos |
| `verificar_pep` | InfoProbidad | Pagada | Todos |
| `verificar_interpol` | Interpol (notificaciones rojas) | Pagada | Todos |
| `verificar_defuncion` | Registro Civil | Pagada | Todos |
| `validar_cedula` | Registro Civil (RUT + n° documento) | Pagada | Todos |
| `validar_telefono` | Compañías telefónicas | Pagada (plan aparte) | Todos |
| `evaluar_prospecto` | Las anteriores combinadas | 4–6 consultas | Todos |
| `iniciar_busqueda_causas` / `ver_busqueda_causas` | Poder Judicial (asíncrono) | Pagada (plan aparte) | Rol `kyc.judicial` |

### Motor de riesgo (`scoring.py`, reglas puras y testeadas)

| Señal | Severidad | Efecto |
|---|---|---|
| RUT inscrito como fallecido | Crítica | `rechazar` |
| Notificación roja de Interpol | Crítica | `rechazar` |
| Nombre declarado no coincide con SII (< 75 %) | Alta | `revision_manual` |
| Cédula no vigente o número de documento no corresponde | Alta | `revision_manual` |
| Teléfono inexistente | Media | `revision_manual` |
| Persona políticamente expuesta | Media | `debida_diligencia_reforzada` (no es señal de fraude) |
| Sin inicio de actividades en SII | Baja | Informativa |
| Defunción o Interpol sin respuesta | — | Nunca `aprobar`: `revision_manual` |

La salida es una **recomendación** con puntaje 0–100, señales con fuente y verificaciones
incompletas. La decisión final es de una persona.

### Detalles de integración con Boostr

- RUT validado localmente antes de gastar una consulta.
- Límite de Boostr (5 req / 10 s) respetado en el cliente; caché de 1 h por instancia para no pagar dos veces.
- Códigos de error traducidos: `U-12`/`U-04` = sin registros (concluyente), `U-03`/`U-10` = fuente caída (no concluyente), `U-06` = servicio no contratado.
- Poder Judicial: el webhook se autentica con un secreto en la URL (Boostr no firma) y el `search_id` va firmado con la identidad del usuario: nadie puede leer búsquedas de otro.

---

## 5. Estructura del repositorio

```
src/
├── mcp_core/            # común a todos los clientes (futuro paquete versionado)
│   ├── config.py        # settings + validaciones de producción
│   ├── auth.py          # AzureProvider / GoogleProvider + Firestore cifrado
│   ├── identity.py      # identidad normalizada, tenant, roles
│   ├── governance.py    # auditoría y rate limit
│   └── app.py           # create_server()
└── boostr_kyc/          # MCP específico del piloto
    ├── rut.py           # validación módulo 11
    ├── client.py        # cliente Boostr (rate limit, caché, errores)
    ├── scoring.py       # motor de reglas
    └── server.py        # tools
tests/                   # 36 tests: RUT, cliente, scoring, servidor, identidad
infra/                   # Terraform: Cloud Run, Firestore, Secret Manager, logs
docs/                    # despliegue en GCP y guía de conexión para usuarios
Dockerfile
```

---

## 6. Fases

| Fase | Entregable | Estado |
|---|---|---|
| **0. Decisiones** | Arquitectura de la sección 1 | ✅ |
| **1. Piloto Boostr** | `mcp_core` + `boostr_kyc`, tests, Dockerfile, Terraform | ✅ código; falta desplegar |
| **2. Despliegue staging** | Proyecto GCP, app en Entra ID de prueba, conexión real desde Claude y ChatGPT | Siguiente |
| **3. Plantilla Copier** | Extraer `mcp_core` a paquete versionado (Artifact Registry Python); `copier copy` genera repo de cliente con IdP, scopes y Terraform | Pendiente |
| **4. Hardening** | SCIM, Cloud Armor opcional, alertas (401/403, errores, costo Boostr), pentest | Pendiente |
| **5. Primer cliente** | Onboarding con su admin de Entra ID, beta cerrada, guía de conexión con su marca | Pendiente |

### Criterios de salida de la fase 2

1. Login SSO completo desde Claude web, Claude Code y ChatGPT (modo desarrollador).
2. Usuario de otro tenant rechazado; usuario sin rol no ve las tools judiciales.
3. Un redeploy no obliga a reconectar (Firestore + clave de firma fija).
4. Eventos de auditoría visibles en el bucket dedicado.

---

## 7. Costos estimados por cliente

| Componente | Costo mensual aproximado |
|---|---|
| Cloud Run, `min_instances=0` | US$0–5 con uso bajo (free tier) |
| Cloud Run, `min_instances=1` (sin arranque en frío) | US$10–20 |
| Firestore, Secret Manager, Cloud Logging | Centavos (free tier) |
| Dominio propio vía load balancer (opcional) | ~US$18, compartible entre clientes |
| **Boostr** | Según plan contratado: es el costo variable principal |

---

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Varios servicios de Boostr están en **beta** y pueden cambiar sin aviso | Tests con respuestas reales grabadas; revisar el changelog de Boostr en cada release |
| Boostr depende de un solo proveedor y de scraping de fuentes públicas | Resultados "no concluyentes" nunca aprueban; diseñar el cliente para poder cambiar de proveedor |
| Límite 5 req / 10 s por instancia | `max_instances` bajo; pedir aumento a Boostr con el plan pagado |
| **Protección de datos personales** (Ley 19.628 y Ley 21.719, que entra en vigencia en diciembre de 2026) | Finalidad declarada en las instrucciones del servidor, minimización (sin documentos de litigantes), auditoría seudonimizada, revisión legal antes de producción |
| Cambios en la spec MCP de autorización | FastMCP fijado por versión; actualizar en `mcp_core` con tests |
| Callbacks OAuth de Claude/ChatGPT cambian | Lista en un solo lugar (`config.py`), verificada en cada release |

---

## 9. Próximos pasos

1. Contratar la API key de Boostr (planes Rutificador y, si aplica, Poder Judicial y Teléfonos).
2. Crear el proyecto GCP de staging y aplicar `infra/` (ver `docs/despliegue-gcp.md`).
3. Registrar la app en un Entra ID de prueba y conectar desde Claude y ChatGPT.
4. Con el piloto validado, extraer la plantilla Copier (fase 3).

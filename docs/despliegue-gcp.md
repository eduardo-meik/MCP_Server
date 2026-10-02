# Despliegue de un MCP en Google Cloud

Guía para desplegar `boostr_kyc` (o cualquier MCP de la plantilla) para un cliente.
Cada cliente tiene su propio servicio Cloud Run. Se recomienda usar un proyecto GCP por cliente.

## 1. Preparar el proyecto

```bash
export PROJECT_ID=meik-mcp-acme REGION=southamerica-west1 CLIENT=acme
gcloud config set project $PROJECT_ID
gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com
gcloud artifacts repositories create mcp --repository-format=docker --location=$REGION
```

La URL de Cloud Run es predecible, así que se conoce antes del primer deploy:

```bash
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
export BASE_URL="https://mcp-$CLIENT-$PROJECT_NUMBER.$REGION.run.app"
```

## 2. Registrar la aplicación en el IdP del cliente

### Opción A: Microsoft Entra ID (Azure AD)

La hace el administrador de Entra del cliente, en **su** tenant:

1. **App registrations → New registration**
   - Supported account types: *Accounts in this organizational directory only*.
   - Redirect URI (Web): `$BASE_URL/auth/callback`.
2. **Certificates & secrets → New client secret.** Guardar el valor.
3. **Expose an API**
   - Application ID URI: `api://<client-id>` (valor por defecto).
   - Add a scope: `mcp.access`, consentimiento *Admins and users*.
4. **Manifest:** `"requestedAccessTokenVersion": 2`.
5. **App roles** (opcional): crear `kyc.judicial` (*Users/Groups*) y asignarlo en
   *Enterprise applications → la app → Users and groups*.
6. **Enterprise applications → Properties → Assignment required = Yes**, para que solo
   las personas o grupos asignados puedan conectarse.
7. **API permissions → Grant admin consent.**

El cliente nos entrega: **Tenant ID**, **Client ID** y el **client secret** por un canal seguro.

### Opción B: Google Workspace

1. En un proyecto GCP: **APIs & Services → OAuth consent screen**.
   - *Internal* si el proyecto está dentro de la organización Workspace del cliente.
   - *External* si está en nuestra organización. El dominio se valida igual en el código.
2. **Credentials → Create OAuth client ID → Web application.**
   - Authorized redirect URI: `$BASE_URL/auth/callback`.
3. Datos que necesitamos: **Client ID**, **client secret** y el dominio (ej. `acme.cl`).

## 3. Construir la imagen

```bash
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT_ID/mcp/boostr-kyc:0.1.0
```

## 4. Crear la infraestructura

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # completar
terraform init
terraform apply
```

La primera vez en el proyecto: `create_firestore_database = true`.

## 5. Cargar los secretos

Terraform crea los secretos vacíos. Para cargar sus valores:

```bash
S=mcp-$CLIENT
openssl rand -base64 48 | gcloud secrets versions add $S-jwt-signing-key --data-file=-
python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode(),end='')" \
  | gcloud secrets versions add $S-storage-encryption-key --data-file=-
openssl rand -hex 32 | gcloud secrets versions add $S-audit-hash-salt --data-file=-
openssl rand -hex 24 | gcloud secrets versions add $S-boostr-webhook-secret --data-file=-
printf '%s' "$BOOSTR_API_KEY"    | gcloud secrets versions add $S-boostr-api-key --data-file=-
printf '%s' "$AZURE_SECRET"      | gcloud secrets versions add $S-azure-client-secret --data-file=-
```

Después de cargarlos, vuelve a ejecutar `terraform apply` (o despliega una nueva revisión) para que Cloud Run los lea.

> No rotes `jwt-signing-key` ni `storage-encryption-key` sin plan: invalidan las conexiones
> existentes y los usuarios deberán reconectar.

## 6. Verificar

```bash
curl -s $BASE_URL/healthz                                    # {"status":"ok"}
curl -si -X POST $BASE_URL/mcp | grep -i www-authenticate     # 401 + resource_metadata
curl -s $BASE_URL/.well-known/oauth-authorization-server | jq .code_challenge_methods_supported
```

Luego conecta desde Claude o ChatGPT siguiendo [conexion.md](conexion.md).

## 7. Operación

- **Auditoría:** Logging → bucket `mcp-<cliente>-audit`, filtro `jsonPayload.log_type="mcp-audit"`.
- **Buscar consultas sobre un RUT:** calcula `HMAC-SHA256(salt, "\"<rut>\"")[:16]` con el salt
  del secreto y busca ese valor en `jsonPayload.args.rut`. El RUT debe ir tal como lo escribió
  el usuario en la llamada.
- **Revocar acceso de una persona:** quitar la asignación en Entra (o suspender la cuenta Google).
  El access token vence en 15 minutos como máximo.
- **Actualizar FastMCP:** cambiar la versión fijada en `pyproject.toml`, correr los tests y
  probar el login en staging antes de producción.

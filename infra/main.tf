# Despliegue de un MCP de la plantilla en Cloud Run (un servicio por cliente).
#
# Los valores de los secretos NO van en Terraform: se crean los contenedores
# y se cargan con `gcloud secrets versions add` (ver docs/despliegue-gcp.md).

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  service = "mcp-${var.client_slug}"
  # Secretos comunes + los del proveedor de identidad elegido.
  secrets = merge(
    {
      MCP_JWT_SIGNING_KEY        = "${local.service}-jwt-signing-key"
      MCP_STORAGE_ENCRYPTION_KEY = "${local.service}-storage-encryption-key"
      MCP_AUDIT_HASH_SALT        = "${local.service}-audit-hash-salt"
      BOOSTR_API_KEY             = "${local.service}-boostr-api-key"
      BOOSTR_WEBHOOK_SECRET      = "${local.service}-boostr-webhook-secret"
    },
    var.auth_provider == "azure" ? { MCP_AZURE_CLIENT_SECRET = "${local.service}-azure-client-secret" } : {},
    var.auth_provider == "google" ? { MCP_GOOGLE_CLIENT_SECRET = "${local.service}-google-client-secret" } : {},
  )
  plain_env = merge(
    {
      MCP_ENVIRONMENT          = var.environment
      MCP_SERVER_NAME          = local.service
      MCP_BASE_URL             = var.base_url
      MCP_AUTH_PROVIDER        = var.auth_provider
      MCP_STORAGE_BACKEND      = "firestore"
      MCP_FIRESTORE_COLLECTION = "${local.service}-oauth"
      MCP_ROLE_ASSIGNMENTS     = jsonencode(var.role_assignments)
    },
    var.auth_provider == "azure" ? {
      MCP_AZURE_TENANT_ID = var.azure_tenant_id
      MCP_AZURE_CLIENT_ID = var.azure_client_id
    } : {},
    var.auth_provider == "google" ? {
      MCP_GOOGLE_CLIENT_ID       = var.google_client_id
      MCP_GOOGLE_ALLOWED_DOMAINS = jsonencode(var.google_allowed_domains)
    } : {},
  )
}

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# Firestore guarda clientes OAuth registrados y tokens upstream (cifrados con Fernet).
# Un proyecto solo tiene una base "(default)": créala una vez por proyecto.
resource "google_firestore_database" "default" {
  count       = var.create_firestore_database ? 1 : 0
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

resource "google_service_account" "mcp" {
  account_id   = local.service
  display_name = "MCP ${var.client_slug}"
}

resource "google_project_iam_member" "firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.mcp.email}"
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = local.secrets
  secret_id = each.value
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = google_secret_manager_secret.secrets
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.mcp.email}"
}

resource "google_cloud_run_v2_service" "mcp" {
  name                = local.service
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.environment == "production"

  template {
    service_account                  = google_service_account.mcp.email
    max_instance_request_concurrency = 40

    scaling {
      min_instance_count = var.min_instances
      # El límite de Boostr (5 req/10 s) se controla por instancia: mantener bajo.
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
      }

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets[env.key].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.access]
}

# El endpoint es público a nivel de red: la autenticación la hace OAuth en la app.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.mcp.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Auditoría: copia los eventos `mcp-audit` a un bucket de logs con retención propia.
resource "google_logging_project_bucket_config" "audit" {
  project        = var.project_id
  location       = var.region
  bucket_id      = "${local.service}-audit"
  retention_days = var.audit_retention_days
  # Bloquear la retención es irreversible: activarlo solo en producción.
  locked = var.lock_audit_retention
}

resource "google_logging_project_sink" "audit" {
  name                   = "${local.service}-audit"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.audit.id}"
  filter                 = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${local.service}\" AND jsonPayload.log_type=\"mcp-audit\""
  unique_writer_identity = true
}

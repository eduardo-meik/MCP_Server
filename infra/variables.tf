variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "southamerica-west1" # Santiago
}

variable "client_slug" {
  description = "Identificador corto del cliente, ej. 'acme'. Define nombres de recursos."
  type        = string
}

variable "environment" {
  type    = string
  default = "production"
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment debe ser staging o production."
  }
}

variable "image" {
  description = "Imagen en Artifact Registry, ej. southamerica-west1-docker.pkg.dev/proj/mcp/boostr-kyc:1.0.0"
  type        = string
}

variable "base_url" {
  description = "URL pública https del servicio (dominio propio o la URL de Cloud Run)."
  type        = string
}

variable "auth_provider" {
  type = string
  validation {
    condition     = contains(["azure", "google"], var.auth_provider)
    error_message = "auth_provider debe ser azure o google."
  }
}

variable "azure_tenant_id" {
  type    = string
  default = ""
}

variable "azure_client_id" {
  type    = string
  default = ""
}

variable "google_client_id" {
  type    = string
  default = ""
}

variable "google_allowed_domains" {
  type    = list(string)
  default = []
}

variable "role_assignments" {
  description = "Roles por email, ej. { \"ana@cliente.cl\" = [\"kyc.judicial\"] }"
  type        = map(list(string))
  default     = {}
}

variable "min_instances" {
  description = "0 = sin costo en reposo (con arranque en frío); 1 = respuesta inmediata."
  type        = number
  default     = 0
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "create_firestore_database" {
  description = "true solo la primera vez en el proyecto."
  type        = bool
  default     = false
}

variable "audit_retention_days" {
  type    = number
  default = 365
}

variable "lock_audit_retention" {
  type    = bool
  default = false
}

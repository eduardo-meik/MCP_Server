output "service_url" {
  value = google_cloud_run_v2_service.mcp.uri
}

output "mcp_endpoint" {
  description = "URL que se pega en Claude / ChatGPT como conector."
  value       = "${var.base_url}/mcp"
}

output "idp_redirect_uri" {
  description = "Único redirect URI que se registra en Entra ID o Google."
  value       = "${var.base_url}/auth/callback"
}

output "secrets_to_load" {
  description = "Secretos a cargar con: gcloud secrets versions add <id> --data-file=-"
  value       = [for s in google_secret_manager_secret.secrets : s.secret_id]
}

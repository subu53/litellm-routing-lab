output "service_url" {
  description = "LiteLLM gateway base URL."
  value       = google_cloud_run_v2_service.gateway.uri
}
output "admin_url" {
  description = "LiteLLM Admin UI URL."
  value       = "${google_cloud_run_v2_service.gateway.uri}/ui"
}
output "image_uri" {
  description = "Artifact Registry image URI used by Cloud Run."
  value       = local.image_uri
}

variable "project_id" {
  description = "The billed Google Cloud project ID."
  type        = string
}
variable "region" {
  description = "The region for Cloud Run, Cloud SQL, and Artifact Registry."
  type        = string
  default     = "us-central1"
}
variable "vertex_location" {
  description = "The Vertex AI location used by both Gemini deployments."
  type        = string
  default     = "global"
}

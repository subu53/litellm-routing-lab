# Define names and connection values reused by the stack.
locals {
  required_services = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
  ])
  repository_id  = "litellm"
  service_name   = "litellm-gateway"
  image_uri      = "${var.region}-docker.pkg.dev/${var.project_id}/${local.repository_id}/litellm:1.104.0"
  runtime_member = "serviceAccount:${google_service_account.gateway.email}"
  database_url   = "postgresql://litellm:${random_password.runtime["database"].result}@localhost/litellm?host=/cloudsql/${google_sql_database_instance.postgres.connection_name}&connection_limit=2"
}

# Enable every Google Cloud API used by the stack.
resource "google_project_service" "apis" {
  for_each           = local.required_services
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_secret_manager_secret" "typesafe_api_key" {
  project   = var.project_id
  secret_id = "typesafe-api-key"
}

# Create the image destination and the gateway identity.
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = local.repository_id
  description   = "LiteLLM gateway images"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

resource "google_service_account" "gateway" {
  project      = var.project_id
  account_id   = "litellm"
  display_name = "LiteLLM gateway"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = toset(["roles/aiplatform.user", "roles/cloudsql.client"])
  project  = var.project_id
  role     = each.value
  member   = local.runtime_member
}

# Generate credentials without storing plaintext values in files.
resource "random_password" "runtime" {
  for_each = { database = 32, master = 48, salt = 48 }
  length   = each.value
  special  = false
}

# Provision the persistent database used by the Admin UI.
resource "google_sql_database_instance" "postgres" {
  project          = var.project_id
  name             = "litellm-postgres"
  region           = var.region
  database_version = "POSTGRES_15"
  settings {
    tier                  = "db-f1-micro"
    edition               = "ENTERPRISE"
    disk_type             = "PD_SSD"
    disk_size             = 10
    disk_autoresize       = false
    connector_enforcement = "REQUIRED"
    backup_configuration { enabled = false }
    ip_configuration { ipv4_enabled = true }
  }
  deletion_protection = false
  depends_on          = [google_project_service.apis]
}

# Create the application database and its user.
resource "google_sql_database" "litellm" {
  project  = var.project_id
  name     = "litellm"
  instance = google_sql_database_instance.postgres.name
}
resource "google_sql_user" "litellm" {
  project  = var.project_id
  name     = "litellm"
  instance = google_sql_database_instance.postgres.name
  password = random_password.runtime["database"].result
}

# Create separate containers for the generated runtime values.
resource "google_secret_manager_secret" "runtime" {
  for_each = {
    master_key   = "litellm-master-key"
    salt_key     = "litellm-salt-key"
    database_url = "litellm-database-url"
  }
  project   = var.project_id
  secret_id = each.value
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Store generated values as the current secret versions.
resource "google_secret_manager_secret_version" "master_key" {
  secret      = google_secret_manager_secret.runtime["master_key"].id
  secret_data = "sk-${random_password.runtime["master"].result}"
}
resource "google_secret_manager_secret_version" "salt_key" {
  secret      = google_secret_manager_secret.runtime["salt_key"].id
  secret_data = "sk-${random_password.runtime["salt"].result}"
}
resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.runtime["database_url"].id
  secret_data = local.database_url
  depends_on  = [google_sql_database.litellm, google_sql_user.litellm]
}

# Map the plain and secret-backed runtime environment variables.
locals {
  runtime_values = {
    VERTEXAI_PROJECT  = var.project_id
    VERTEXAI_LOCATION = var.vertex_location
    STORE_MODEL_IN_DB = "True"
  }
  runtime_secrets = {
    LITELLM_MASTER_KEY = google_secret_manager_secret.runtime["master_key"].secret_id
    LITELLM_SALT_KEY   = google_secret_manager_secret.runtime["salt_key"].secret_id
    DATABASE_URL       = google_secret_manager_secret.runtime["database_url"].secret_id
    TYPESAFE_API_KEY   = data.google_secret_manager_secret.typesafe_api_key.secret_id
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_secret_access" {
  for_each  = local.runtime_secrets
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = local.runtime_member
}

# Define the public gateway service and its scaling limits.
resource "google_cloud_run_v2_service" "gateway" {
  project             = var.project_id
  name                = local.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  template {
    service_account = google_service_account.gateway.email
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = local.image_uri
      ports { container_port = 8080 }
      resources {
        limits            = { cpu = "1", memory = "2Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.runtime_values
        content {
          name  = env.key
          value = env.value
        }
      }
      # Inject runtime secrets without placing them in the image.
      dynamic "env" {
        for_each = local.runtime_secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      # Mount the managed Cloud SQL connection inside the container.
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.postgres.connection_name]
      }
    }
  }
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
  depends_on = [
    google_project_iam_member.runtime_roles,
    google_secret_manager_secret_iam_member.runtime_secret_access,
    google_secret_manager_secret_version.master_key,
    google_secret_manager_secret_version.salt_key,
    google_secret_manager_secret_version.database_url,
    google_sql_user.litellm,
  ]
}

# Allow unauthenticated HTTP requests to reach the gateway.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.gateway.location
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

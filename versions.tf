terraform {
  required_version = "= 1.16.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
  }
}
provider "google" {
  project = var.project_id
  region  = var.region
}

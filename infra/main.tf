# Free-tier-friendly GCP footprint for Metric On-Call:
#   GCS landing bucket · BigQuery datasets raw + analytics · Artifact Registry · Cloud Run job (loader)
#   · Cloud Scheduler nightly trigger · Secret Manager slot for the Gemini key.
# Apply through scripts/deploy_gcp.sh (it builds the image between the two applies).
# `make cloud-down` destroys everything.
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}

variable "project" { type = string }
variable "region" {
  type    = string
  default = "australia-southeast1"
}
variable "enable_schedule" {
  type        = bool
  default     = false
  description = "Create the nightly Cloud Scheduler trigger. Off by default: runs are manual, so nothing consumes credits unattended."
}
variable "loader_image" {
  type        = string
  default     = ""
  description = "Container image for the loader job (built from pipelines/Dockerfile). Empty = skip the Cloud Run job and nightly schedule."
}

provider "google" {
  project = var.project
  region  = var.region
}

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "moc" {
  repository_id = "moc"
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

resource "google_storage_bucket" "landing" {
  name                        = "${var.project}-metric-on-call-landing"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  lifecycle_rule {
    condition { age = 30 }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

resource "google_bigquery_dataset" "raw" {
  dataset_id                 = "raw"
  location                   = var.region
  delete_contents_on_destroy = true
  depends_on                 = [google_project_service.apis]
}

resource "google_bigquery_dataset" "analytics" {
  dataset_id                 = "analytics"
  location                   = var.region
  delete_contents_on_destroy = true
  depends_on                 = [google_project_service.apis]
}

resource "google_service_account" "loader" {
  account_id   = "moc-loader"
  display_name = "Metric On-Call loader"
  depends_on   = [google_project_service.apis]
}

# the loader moves delivered files inbox/ -> loaded/, so it needs object admin on this one bucket
resource "google_storage_bucket_iam_member" "loader_files" {
  bucket = google_storage_bucket.landing.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.loader.email}"
}

resource "google_bigquery_dataset_iam_member" "loader_writes" {
  dataset_id = google_bigquery_dataset.raw.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.loader.email}"
}

resource "google_project_iam_member" "loader_jobs" {
  project = var.project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.loader.email}"
}

resource "google_secret_manager_secret" "gemini" {
  secret_id = "gemini-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job" "loader" {
  count               = var.loader_image == "" ? 0 : 1
  name                = "moc-loader"
  location            = var.region
  deletion_protection = false
  template {
    template {
      service_account = google_service_account.loader.email
      max_retries     = 0 # a retry after a partial load would double-load files
      containers {
        image = var.loader_image
        env {
          name  = "LANDING_BUCKET"
          value = google_storage_bucket.landing.name
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project
        }
        env {
          name  = "WAREHOUSE"
          value = "bigquery"
        }
      }
    }
  }
  depends_on = [google_project_service.apis]
}

# the scheduler's identity must be allowed to run the job
resource "google_cloud_run_v2_job_iam_member" "scheduler_invokes" {
  count    = var.loader_image == "" ? 0 : 1
  project  = var.project
  location = var.region
  name     = google_cloud_run_v2_job.loader[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.loader.email}"
}

resource "google_cloud_scheduler_job" "nightly" {
  count      = var.enable_schedule && var.loader_image != "" ? 1 : 0
  name       = "moc-nightly-load"
  schedule   = "30 2 * * *"
  time_zone  = "Australia/Sydney"
  region     = var.region
  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invokes]
  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project}/jobs/${google_cloud_run_v2_job.loader[0].name}:run"
    oauth_token { service_account_email = google_service_account.loader.email }
  }
}

output "landing_bucket" { value = google_storage_bucket.landing.name }
output "loader_image_repo" { value = "${var.region}-docker.pkg.dev/${var.project}/moc" }

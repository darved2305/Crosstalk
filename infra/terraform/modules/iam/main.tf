variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "artifact_bucket" { type = string }
variable "restricted_bucket" { type = string }
variable "repository_id" { type = string }
variable "github_repository" { type = string }

resource "google_service_account" "gpu" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-gpu"
  display_name = "Crosstalk GPU server"
}

resource "google_service_account" "client" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-client"
  display_name = "Crosstalk collection client"
}

resource "google_service_account" "analysis" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-analysis"
  display_name = "Observer-only Crosstalk analysis"
}

resource "google_service_account" "github" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-github"
  display_name = "Keyless GitHub deployment identity"
}

resource "google_storage_bucket_iam_member" "gpu_artifacts" {
  bucket = var.artifact_bucket
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.gpu.email}"
}
resource "google_storage_bucket_iam_member" "client_artifacts" {
  bucket = var.artifact_bucket
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.client.email}"
}
resource "google_storage_bucket_iam_member" "client_restricted" {
  bucket = var.restricted_bucket
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.client.email}"
}
resource "google_storage_bucket_iam_member" "analysis_artifacts" {
  bucket = var.artifact_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.analysis.email}"
}

resource "google_project_iam_member" "instance_logging" {
  for_each = toset([google_service_account.gpu.email, google_service_account.client.email])
  project  = var.project_id
  role     = "roles/logging.logWriter"
  member   = "serviceAccount:${each.value}"
}
resource "google_project_iam_member" "instance_metrics" {
  for_each = toset([google_service_account.gpu.email, google_service_account.client.email])
  project  = var.project_id
  role     = "roles/monitoring.metricWriter"
  member   = "serviceAccount:${each.value}"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "Crosstalk GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}'"
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}

resource "google_service_account_iam_member" "github_wif" {
  service_account_id = google_service_account.github.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository_iam_member" "github_registry" {
  project    = var.project_id
  location   = var.region
  repository = var.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github.email}"
}

output "gpu_service_account" { value = google_service_account.gpu.email }
output "client_service_account" { value = google_service_account.client.email }
output "analysis_service_account" { value = google_service_account.analysis.email }
output "github_service_account" { value = google_service_account.github.email }
output "github_wif_provider" { value = google_iam_workload_identity_pool_provider.github.name }

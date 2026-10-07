variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }

resource "google_artifact_registry_repository" "main" {
  project                = var.project_id
  location               = var.region
  repository_id          = var.name_prefix
  description            = "Digest-pinned Crosstalk runtime images"
  format                 = "DOCKER"
  cleanup_policy_dry_run = true
}

output "repository_id" { value = google_artifact_registry_repository.main.repository_id }
output "repository_url" { value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.main.repository_id}" }

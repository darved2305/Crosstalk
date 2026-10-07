variable "project_id" { type = string }
variable "location" { type = string }
variable "name_prefix" { type = string }

resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.name_prefix}-artifacts"
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  lifecycle_rule {
    condition {
      age                   = 30
      matches_prefix        = ["sessions/"]
      matches_storage_class = ["STANDARD"]
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
  lifecycle { prevent_destroy = true }
}

resource "google_storage_bucket" "restricted" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.name_prefix}-restricted"
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  lifecycle { prevent_destroy = true }
}

output "artifact_bucket" { value = google_storage_bucket.artifacts.name }
output "restricted_bucket" { value = google_storage_bucket.restricted.name }

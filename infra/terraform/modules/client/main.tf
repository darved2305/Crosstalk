variable "project_id" { type = string }
variable "zone" { type = string }
variable "name_prefix" { type = string }
variable "subnet_self_link" { type = string }
variable "service_account" { type = string }
variable "artifact_bucket" { type = string }
variable "restricted_bucket" { type = string }
variable "gpu_internal_ip" { type = string }
variable "loadgen_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.loadgen_image))
    error_message = "Load-generator image must be digest-pinned."
  }
}
variable "capture_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.capture_image))
    error_message = "Capture image must be digest-pinned."
  }
}
variable "startup_script" { type = string }
variable "compose_config" { type = string }

data "google_compute_image" "debian" {
  project = "debian-cloud"
  family  = "debian-12"
}

resource "google_compute_instance" "main" {
  project      = var.project_id
  zone         = var.zone
  name         = "${var.name_prefix}-client"
  machine_type = "c3-standard-4"
  tags         = ["${var.name_prefix}-client", "${var.name_prefix}-ssh"]
  boot_disk {
    initialize_params {
      image = data.google_compute_image.debian.self_link
      size  = 80
      type  = "pd-balanced"
    }
  }
  network_interface { subnetwork = var.subnet_self_link }
  service_account {
    email  = var.service_account
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
  metadata = {
    enable-oslogin = "TRUE"
    startup-script = var.startup_script
    client-compose = var.compose_config
    ct-config      = jsonencode({ artifact_bucket = var.artifact_bucket, restricted_bucket = var.restricted_bucket, gpu_internal_ip = var.gpu_internal_ip, loadgen_image = var.loadgen_image, capture_image = var.capture_image })
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
}

output "internal_ip" { value = google_compute_instance.main.network_interface[0].network_ip }

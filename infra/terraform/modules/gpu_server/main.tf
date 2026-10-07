variable "project_id" { type = string }
variable "zone" { type = string }
variable "name_prefix" { type = string }
variable "subnet_self_link" { type = string }
variable "service_account" { type = string }
variable "artifact_bucket" { type = string }
variable "model_id" { type = string }
variable "model_revision" { type = string }
variable "vllm_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.vllm_image))
    error_message = "vLLM image must be digest-pinned."
  }
}
variable "shaper_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.shaper_image))
    error_message = "Shaper image must be digest-pinned."
  }
}
variable "startup_script" { type = string }
variable "compose_config" { type = string }
variable "policy_config" { type = string }
variable "machine_type" { type = string }
variable "accelerator_type" { type = string }
variable "accelerator_count" { type = number }

data "google_compute_image" "dlvm" {
  project = "deeplearning-platform-release"
  family  = "common-cu128-ubuntu-2204-nvidia-570"
}

resource "google_compute_instance" "main" {
  project                   = var.project_id
  zone                      = var.zone
  name                      = "${var.name_prefix}-gpu"
  machine_type              = var.machine_type
  tags                      = ["${var.name_prefix}-gpu", "${var.name_prefix}-ssh"]
  allow_stopping_for_update = true
  boot_disk {
    initialize_params {
      image = data.google_compute_image.dlvm.self_link
      size  = 150
      type  = "pd-balanced"
    }
  }
  guest_accelerator {
    type  = var.accelerator_type
    count = var.accelerator_count
  }
  scheduling {
    on_host_maintenance = "TERMINATE"
    automatic_restart   = true
    provisioning_model  = "STANDARD"
  }
  network_interface { subnetwork = var.subnet_self_link }
  service_account {
    email  = var.service_account
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
  metadata = {
    enable-oslogin = "TRUE"
    startup-script = var.startup_script
    gpu-compose    = var.compose_config
    policies       = var.policy_config
    ct-config      = jsonencode({ artifact_bucket = var.artifact_bucket, model_id = var.model_id, model_revision = var.model_revision, vllm_image = var.vllm_image, shaper_image = var.shaper_image })
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
}

output "internal_ip" { value = google_compute_instance.main.network_interface[0].network_ip }

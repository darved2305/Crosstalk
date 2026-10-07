variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "operator_cidrs" { type = list(string) }

resource "google_compute_network" "main" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
  mtu                     = 1460
}

resource "google_compute_subnetwork" "main" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.main.id
  ip_cidr_range            = "10.40.0.0/20"
  private_ip_google_access = true
}

resource "google_compute_router" "main" {
  project = var.project_id
  name    = "${var.name_prefix}-router"
  region  = var.region
  network = google_compute_network.main.id
}

resource "google_compute_router_nat" "main" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-nat"
  region                             = var.region
  router                             = google_compute_router.main.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

resource "google_compute_firewall" "iap_ssh" {
  project       = var.project_id
  name          = "${var.name_prefix}-iap-ssh"
  network       = google_compute_network.main.name
  source_ranges = concat(["35.235.240.0/20"], var.operator_cidrs)
  target_tags   = ["${var.name_prefix}-ssh"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "measured_leg" {
  project     = var.project_id
  name        = "${var.name_prefix}-measured-leg"
  network     = google_compute_network.main.name
  source_tags = ["${var.name_prefix}-client"]
  target_tags = ["${var.name_prefix}-gpu"]
  allow {
    protocol = "tcp"
    ports    = ["8443", "9100"]
  }
}

output "subnet_self_link" { value = google_compute_subnetwork.main.self_link }
output "network_name" { value = google_compute_network.main.name }

module "network" {
  source         = "./modules/network"
  project_id     = var.project_id
  region         = var.region
  name_prefix    = var.name_prefix
  operator_cidrs = var.operator_cidrs
}

module "storage" {
  source      = "./modules/storage"
  project_id  = var.project_id
  location    = var.artifact_bucket_location
  name_prefix = var.name_prefix
}

module "registry" {
  source      = "./modules/registry"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix
}

module "iam" {
  source            = "./modules/iam"
  project_id        = var.project_id
  region            = var.region
  name_prefix       = var.name_prefix
  artifact_bucket   = module.storage.artifact_bucket
  restricted_bucket = module.storage.restricted_bucket
  repository_id     = module.registry.repository_id
  github_repository = var.github_repository
}

module "gpu_server" {
  source            = "./modules/gpu_server"
  project_id        = var.project_id
  zone              = var.zone
  name_prefix       = var.name_prefix
  subnet_self_link  = module.network.subnet_self_link
  service_account   = module.iam.gpu_service_account
  artifact_bucket   = module.storage.artifact_bucket
  model_id          = var.model_id
  model_revision    = var.model_revision
  vllm_image        = var.vllm_image
  shaper_image      = var.shaper_image
  machine_type      = var.gpu_machine_type
  accelerator_type  = var.gpu_accelerator_type
  accelerator_count = var.gpu_accelerator_count
  startup_script    = file("${path.root}/../../deploy/gce/gpu-startup.sh")
  compose_config    = file("${path.root}/../../deploy/gce/gpu-compose.yml")
  policy_config     = file("${path.root}/../../experiments/policies/arms.yaml")
  depends_on        = [module.network, module.iam]
}

module "client" {
  source            = "./modules/client"
  project_id        = var.project_id
  zone              = var.zone
  name_prefix       = var.name_prefix
  subnet_self_link  = module.network.subnet_self_link
  service_account   = module.iam.client_service_account
  artifact_bucket   = module.storage.artifact_bucket
  restricted_bucket = module.storage.restricted_bucket
  gpu_internal_ip   = module.gpu_server.internal_ip
  loadgen_image     = var.loadgen_image
  capture_image     = var.capture_image
  startup_script    = file("${path.root}/../../deploy/gce/client-startup.sh")
  compose_config    = file("${path.root}/../../deploy/gce/client-compose.yml")
  depends_on        = [module.gpu_server]
}

module "budget" {
  source             = "./modules/budget"
  project_id         = var.project_id
  billing_account_id = var.billing_account_id
  amount_usd         = var.budget_amount_usd
  name_prefix        = var.name_prefix
}

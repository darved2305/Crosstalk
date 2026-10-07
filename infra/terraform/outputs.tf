output "gpu_internal_ip" {
  value       = module.gpu_server.internal_ip
  description = "Private shaper/vLLM address."
}
output "client_internal_ip" {
  value       = module.client.internal_ip
  description = "Private collection client address; connect through IAP."
}
output "artifact_bucket" {
  value       = module.storage.artifact_bucket
  description = "Observer and reportable artifact bucket."
}
output "restricted_bucket" {
  value       = module.storage.restricted_bucket
  description = "Restricted plaintext/keylog bucket."
}
output "registry" {
  value       = module.registry.repository_url
  description = "Artifact Registry repository URL."
}
output "github_wif_provider" {
  value       = module.iam.github_wif_provider
  description = "Provider name for google-github-actions/auth."
}

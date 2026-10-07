variable "project_id" {
  type        = string
  description = "GCP project that owns the deployment."
}
variable "billing_account_id" {
  type        = string
  description = "Billing account used for budget alerts."
}
variable "region" {
  type        = string
  default     = "us-central1"
  description = "Primary GCP region."
}
variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "Primary compute zone with L4 capacity."
}
variable "name_prefix" {
  type        = string
  default     = "crosstalk"
  description = "Stable prefix for resources."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "Use 2-21 lowercase letters, digits, or hyphens."
  }
}
variable "artifact_bucket_location" {
  type        = string
  default     = "US"
  description = "Dual/multi-region for artifact storage."
}
variable "model_id" {
  type        = string
  default     = "Qwen/Qwen2.5-7B-Instruct"
  description = "Pinned model ID; pair with model_revision."
}
variable "model_revision" {
  type        = string
  description = "Immutable Hugging Face model revision."
}
variable "vllm_image" {
  type        = string
  description = "vLLM image pinned by sha256 digest."
}
variable "shaper_image" {
  type        = string
  description = "Crosstalk shaper image pinned by sha256 digest."
}
variable "loadgen_image" {
  type        = string
  description = "Crosstalk load-generator image pinned by sha256 digest."
}
variable "capture_image" {
  type        = string
  description = "Crosstalk capture image pinned by sha256 digest."
}
variable "gpu_machine_type" {
  type        = string
  default     = "g2-standard-8"
  description = "GPU VM machine type. For a T4 fallback use n1-standard-8."
}
variable "gpu_accelerator_type" {
  type        = string
  default     = "nvidia-l4"
  description = "GPU accelerator type. For a T4 fallback use nvidia-tesla-t4."
}
variable "gpu_accelerator_count" {
  type        = number
  default     = 1
  description = "Number of accelerators attached to the inference VM."
  validation {
    condition     = var.gpu_accelerator_count >= 1 && floor(var.gpu_accelerator_count) == var.gpu_accelerator_count
    error_message = "GPU accelerator count must be a positive integer."
  }
}
variable "operator_cidrs" {
  type        = list(string)
  default     = []
  description = "Optional SSH source ranges; IAP is always allowed."
}
variable "github_repository" {
  type        = string
  description = "GitHub owner/repository authorized through WIF."
}
variable "budget_amount_usd" {
  type        = number
  default     = 100
  description = "Monthly budget alert threshold."
  validation {
    condition     = var.budget_amount_usd > 0
    error_message = "Budget must be positive."
  }
}

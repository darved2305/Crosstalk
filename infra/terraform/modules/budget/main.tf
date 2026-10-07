variable "project_id" { type = string }
variable "billing_account_id" { type = string }
variable "amount_usd" { type = number }
variable "name_prefix" { type = string }

data "google_project" "current" { project_id = var.project_id }

resource "google_billing_budget" "main" {
  billing_account = var.billing_account_id
  display_name    = "${var.name_prefix}-monthly-budget"
  budget_filter { projects = ["projects/${data.google_project.current.number}"] }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(floor(var.amount_usd))
    }
  }
  dynamic "threshold_rules" {
    for_each = toset([0.5, 0.8, 1.0])
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }
}

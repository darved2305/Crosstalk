# GCP deployment

This root provisions the isolated measurement network, an L4 inference VM, a
separate collection VM, versioned artifact and restricted buckets, Artifact
Registry, Workload Identity Federation, least-privilege service accounts, and
a monthly billing budget. Neither VM has a public IP. Operator access uses IAP;
outbound package and image access uses Cloud NAT.

## Prerequisites

Enable Compute Engine, IAM, IAM Credentials, Service Usage, Artifact Registry,
Cloud Storage, Cloud Billing Budgets, and Cloud Resource Manager APIs. Create a
dedicated GCS Terraform-state bucket with versioning and public-access
prevention before initializing this root. The deployer needs resource creation
permissions and billing-account budget permissions; runtime identities do not.

All four runtime image values must use immutable `@sha256:` references. Set
`model_revision` to an immutable upstream commit, not a branch or tag.

```bash
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config="bucket=YOUR_STATE_BUCKET" \
  -backend-config="prefix=crosstalk/prod"
terraform plan -out=tfplan
terraform apply tfplan
```

For local static validation without credentials or a state backend:

```bash
terraform init -backend=false
terraform fmt -recursive -check
terraform validate
```

The default is one L4 on `g2-standard-8`. If L4 quota or capacity is
unavailable, set both `gpu_machine_type = "n1-standard-8"` and
`gpu_accelerator_type = "nvidia-tesla-t4"`. This changes performance and must
be recorded as a distinct experimental environment.

Startup output is available in the serial console and system journal. The GPU
VM publishes only the shaper port to the client tag; vLLM remains on the Docker
network. The generated short-lived lab CA certificate is copied through the
artifact bucket. Restricted plaintext/keylog data can only be created by the
collection identity and is deliberately unreadable by the analysis identity.

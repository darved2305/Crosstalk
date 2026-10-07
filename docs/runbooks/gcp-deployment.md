# GCP deployment runbook

The Terraform-specific prerequisites and commands are in
`infra/terraform/README.md`. Keep Terraform state in a separately bootstrapped,
versioned GCS bucket. Use the manual infrastructure workflow for a reviewed
plan; protect the `production` GitHub environment with required reviewers
before enabling apply.

The repository-created GitHub identity can write only to its Artifact Registry
repository. Terraform automation deliberately expects a separate pre-existing
deployment identity supplied through `GCP_TERRAFORM_WIF_PROVIDER` and
`GCP_TERRAFORM_SERVICE_ACCOUNT`; do not expand the image publisher's role.

After apply, inspect serial output and service state through IAP:

```bash
gcloud compute ssh crosstalk-gpu --zone ZONE --tunnel-through-iap --command 'sudo journalctl -u google-startup-scripts.service -n 200 --no-pager'
gcloud compute ssh crosstalk-client --zone ZONE --tunnel-through-iap --command 'sudo docker ps --no-trunc'
```

If L4 allocation fails, change both documented T4 fallback variables and create
a new plan. Do not silently substitute hardware within an experiment. Record
the accelerator, machine type, image digests, model revision, and Terraform
commit in the run manifest before collection.

To recover a bootstrap failure, fix the cause and rerun the metadata startup
script through the guest agent or recreate only the affected VM. Artifact and
restricted buckets have `prevent_destroy`; never bypass it during routine
recovery. The analysis identity has no access to the restricted bucket.

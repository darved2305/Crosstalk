# Google Cloud console evidence — 2026-10-12

Actual screenshots of project `inspired-campus-436516-k4` (display name `db-opt`),
taken on 2026-10-12 at about 09:45 UTC. The pages were opened in the project
owner's signed-in Google Chrome and captured from the screen. Nothing was clicked
or changed. A strip on the right edge of every image was cropped off to remove the
account avatar. No e-mail address, name or billing-account identifier appears.

| File | Page | What it shows |
|---|---|---|
| `01-project-dashboard.png` | Home → Dashboard | Project name `db-opt`, project ID, project number |
| `02-billing-not-linked.png` | Billing → Linked account | "This project has no billing account" |
| `03-compute-engine-vms.png` | Compute Engine → VM instances | One pre-existing VM, `cfl-box` (us-central1-a), with a stopped-state icon and no external IP |
| `04-cloud-run-overview.png` | Cloud Run → Overview | One pre-existing service, `billing-killswitch` (last updated 25 Aug 2026) |
| `05-enabled-apis.png` | APIs and services | Recent API traffic, with 100% errors on the Compute Engine, Artifact Registry and Eventarc APIs |

**None of the cloud resources shown are CrossTalk.** `cfl-box` and the
`billing-killswitch` Cloud Run service existed before this work. They are
unrelated to this repository and were neither created nor modified by it. The API
errors are consistent with calls made while billing is disabled. That includes
the read-only `gcloud compute instances list` audit, which fails with
"This API method requires billing to be enabled".

A read-only gcloud check at 2026-10-12T09:49:08Z agreed with the console:
`gcloud billing projects describe inspired-campus-436516-k4` returned
`billingEnabled: False` with an empty billing account name.

**Deployment status:** no CrossTalk component is deployed to Google Cloud. The
Terraform in `infra/terraform` (GCE L4 GPU server, client VM, Artifact Registry,
budget) needs an active billing account. That includes free-tier resources:
<https://docs.cloud.google.com/free/docs/free-cloud-features>. Enabling billing
and running paid GPU or VM resources are outside the constraints of this project
(at most ₹1 spend, no billing changes), so the cloud deployment remains blocked.
It has not been simulated.

#!/usr/bin/env bash
# Deletes the VM (and its disk) and the IAP firewall rule so nothing keeps billing.
set -euo pipefail
PROJECT="${PROJECT:?set PROJECT}"
ZONE="${ZONE:-us-central1-a}"
VM="${VM:-crosstalk-lab}"
gcloud --project "$PROJECT" compute instances delete "$VM" --zone "$ZONE" --quiet
gcloud --project "$PROJECT" compute firewall-rules delete "$VM-iap-ssh" --quiet

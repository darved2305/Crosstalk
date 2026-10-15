#!/usr/bin/env bash
# Creates one CPU VM, ships the current commit to it, and runs the Docker
# validation ledger (docs/DOCKER_BLOCKED_VALIDATION.md) there. No GPU: the lab
# runs against the mock upstream and every session is non-reportable.
#
# Usage (Git Bash or Linux, from the repository root):
#   PROJECT=refined-haven-511705-v5 deploy/cloud-vm/up.sh
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT}"
ZONE="${ZONE:-us-central1-a}"
VM="${VM:-crosstalk-lab}"
MACHINE="${MACHINE:-e2-standard-4}"

gc() { gcloud --project "$PROJECT" "$@"; }

if [[ "$(gc billing projects describe "$PROJECT" --format='value(billingEnabled)')" != "True" ]]; then
  echo "Billing is not enabled on $PROJECT. Link an open billing account first." >&2
  exit 1
fi

gc services enable compute.googleapis.com iap.googleapis.com

gc compute firewall-rules describe "$VM-iap-ssh" >/dev/null 2>&1 ||
  gc compute firewall-rules create "$VM-iap-ssh" --network default \
    --direction INGRESS --action allow --rules tcp:22 \
    --source-ranges 35.235.240.0/20 --target-tags "$VM"

gc compute instances describe "$VM" --zone "$ZONE" >/dev/null 2>&1 ||
  gc compute instances create "$VM" --zone "$ZONE" --machine-type "$MACHINE" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 60GB --boot-disk-type pd-balanced \
    --tags "$VM" --shielded-secure-boot --shielded-vtpm \
    --metadata-from-file startup-script=deploy/cloud-vm/vm-bootstrap.sh

echo "Waiting for the VM bootstrap (Docker + uv) ..."
until gc compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap \
  --command 'test -f /var/lib/crosstalk-bootstrapped' >/dev/null 2>&1; do
  sleep 15
done

archive="$(mktemp -d)/crosstalk.tar.gz"
git archive --format=tar.gz -o "$archive" HEAD
gc compute scp "$archive" deploy/cloud-vm/validate.sh "$VM:~/" \
  --zone "$ZONE" --tunnel-through-iap

gc compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap --command \
  "rm -rf ~/crosstalk && mkdir ~/crosstalk && tar -xzf ~/crosstalk.tar.gz -C ~/crosstalk \
   && CT_CODE_COMMIT=$(git rev-parse HEAD) bash ~/validate.sh"

echo
echo "Validation finished. Open the console with: PROJECT=$PROJECT deploy/cloud-vm/tunnel.sh"

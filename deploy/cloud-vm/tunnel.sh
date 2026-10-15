#!/usr/bin/env bash
# Forwards the VM's console (3000) and API (8080) to localhost over IAP.
# Keep it running, then open http://localhost:3000 in a local browser.
set -euo pipefail
PROJECT="${PROJECT:?set PROJECT}"
ZONE="${ZONE:-us-central1-a}"
VM="${VM:-crosstalk-lab}"
gcloud --project "$PROJECT" compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap \
  -- -N -L 3000:127.0.0.1:3000 -L 8080:127.0.0.1:8080

#!/usr/bin/env bash
set -euo pipefail
umask 027

metadata='http://metadata.google.internal/computeMetadata/v1/instance/attributes/ct-config'
config="$(curl --fail --silent --show-error -H 'Metadata-Flavor: Google' "$metadata")"
install -d -m 0750 /opt/crosstalk /var/lib/crosstalk/artifacts
printf '%s' "$config" > /opt/crosstalk/config.json
curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/attributes/client-compose' \
  > /opt/crosstalk/client-compose.yml

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends docker.io jq
if apt-get install -y --no-install-recommends docker-compose-v2; then
  compose=(docker compose)
elif apt-get install -y --no-install-recommends docker-compose-plugin; then
  compose=(docker compose)
else
  apt-get install -y --no-install-recommends docker-compose
  compose=(docker-compose)
fi
systemctl enable --now docker
export LOADGEN_IMAGE="$(jq -r .loadgen_image /opt/crosstalk/config.json)"
export CAPTURE_IMAGE="$(jq -r .capture_image /opt/crosstalk/config.json)"
export GPU_INTERNAL_IP="$(jq -r .gpu_internal_ip /opt/crosstalk/config.json)"
export ARTIFACT_BUCKET="$(jq -r .artifact_bucket /opt/crosstalk/config.json)"
for attempt in $(seq 1 30); do
  access_token="$(curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
    'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' | jq -r .access_token)"
  if curl --fail --silent --show-error -H "Authorization: Bearer ${access_token}" \
    "https://storage.googleapis.com/storage/v1/b/${ARTIFACT_BUCKET}/o/bootstrap%2Flab.crt?alt=media" \
    -o /opt/crosstalk/lab.crt; then break; fi
  if [ "$attempt" -eq 30 ]; then echo 'TLS CA did not become available' >&2; exit 1; fi
  sleep 10
done
docker pull "$LOADGEN_IMAGE"
docker pull "$CAPTURE_IMAGE"
"${compose[@]}" -f /opt/crosstalk/client-compose.yml up -d

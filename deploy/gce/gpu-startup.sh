#!/usr/bin/env bash
set -euo pipefail
umask 027

metadata='http://metadata.google.internal/computeMetadata/v1/instance/attributes/ct-config'
config="$(curl --fail --silent --show-error -H 'Metadata-Flavor: Google' "$metadata")"
install -d -m 0750 /opt/crosstalk /var/lib/crosstalk/artifacts /var/lib/crosstalk/shaper-log
printf '%s' "$config" > /opt/crosstalk/config.json
curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/attributes/gpu-compose' \
  > /opt/crosstalk/gpu-compose.yml
curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/attributes/policies' \
  > /opt/crosstalk/policies.yaml

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends docker.io ethtool jq openssl
if apt-get install -y --no-install-recommends docker-compose-v2; then
  compose=(docker compose)
elif apt-get install -y --no-install-recommends docker-compose-plugin; then
  compose=(docker compose)
else
  apt-get install -y --no-install-recommends docker-compose
  compose=(docker-compose)
fi
systemctl enable --now docker

export VLLM_IMAGE="$(jq -r .vllm_image /opt/crosstalk/config.json)"
export SHAPER_IMAGE="$(jq -r .shaper_image /opt/crosstalk/config.json)"
export MODEL_ID="$(jq -r .model_id /opt/crosstalk/config.json)"
export MODEL_REVISION="$(jq -r .model_revision /opt/crosstalk/config.json)"
export ARTIFACT_BUCKET="$(jq -r .artifact_bucket /opt/crosstalk/config.json)"
install -d -m 0750 -o 65532 -g 65532 /opt/crosstalk/secrets
openssl req -x509 -newkey rsa:3072 -nodes -days 14 -subj '/CN=shaper' \
  -addext 'subjectAltName=DNS:shaper,IP:127.0.0.1' \
  -keyout /opt/crosstalk/secrets/tls.key -out /opt/crosstalk/secrets/tls.crt
chown 65532:65532 /opt/crosstalk/secrets/tls.*
access_token="$(curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' | jq -r .access_token)"
curl --fail --silent --show-error -X POST -H "Authorization: Bearer ${access_token}" \
  -H 'Content-Type: application/x-pem-file' --data-binary @/opt/crosstalk/secrets/tls.crt \
  "https://storage.googleapis.com/upload/storage/v1/b/${ARTIFACT_BUCKET}/o?uploadType=media&name=bootstrap%2Flab.crt"
docker pull "$VLLM_IMAGE"
docker pull "$SHAPER_IMAGE"
"${compose[@]}" -f /opt/crosstalk/gpu-compose.yml up -d

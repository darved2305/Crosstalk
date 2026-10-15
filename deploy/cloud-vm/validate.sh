#!/usr/bin/env bash
# Runs on the VM. Executes docs/DOCKER_BLOCKED_VALIDATION.md in order, stopping
# at the first failure, and keeps a transcript in ~/validation-<timestamp>.log.
set -euo pipefail
cd ~/crosstalk
log=~/validation-$(date -u +%Y%m%dT%H%M%SZ).log
exec > >(tee "$log") 2>&1

run() { echo; echo "\$ $*"; "$@"; }
dc() { sudo docker compose "$@"; }

if [[ ! -f deploy/compose/platform.env ]]; then
  (
    umask 077
    cat >deploy/compose/platform.env <<EOF
CT_POSTGRES_PASSWORD=$(openssl rand -hex 24)
CT_API_OPERATOR_TOKEN=$(openssl rand -hex 24)
CT_API_ORACLE_TOKEN=$(openssl rand -hex 24)
CT_CODE_COMMIT=${CT_CODE_COMMIT:-unknown}
EOF
  )
fi
mkdir -p artifacts
# The worker writes into the same bind mount as the lab containers, so it runs
# as the host user rather than the image's 10001.
export CT_WORKER_USER="$(id -u):$(id -g)"
P=(-f deploy/compose/platform.yml --env-file deploy/compose/platform.env)

run sudo docker version
run dc "${P[@]}" config --quiet
run dc "${P[@]}" build --pull
run dc "${P[@]}" up -d --wait
run dc "${P[@]}" ps
run curl --fail -sS http://127.0.0.1:8080/healthz
run curl --fail -sS http://127.0.0.1:8080/readyz
run curl --fail -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/

run uv sync --locked --all-extras
run uv run crosstalk lab certs
export CT_UID=$(id -u) CT_GID=$(id -g)
run dc -f deploy/compose/lab.yml --profile mock config --quiet
run dc -f deploy/compose/lab.yml --profile mock up -d --build --wait
run uv run crosstalk lab provenance
run dc -f deploy/compose/lab.yml exec loadgen \
  crosstalk session run /app/experiments/validation.yaml --root /artifacts

echo
echo "ALL VALIDATION STEPS PASSED. Transcript: $log"
echo "Operator token for the console: $(grep CT_API_OPERATOR_TOKEN deploy/compose/platform.env | cut -d= -f2)"

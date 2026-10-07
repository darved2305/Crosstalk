# Docker-blocked validation ledger

Docker Engine was unavailable during the 2026-10-07 implementation pass. No
container build, Compose startup, packet-capture capability check, GPU runtime,
or live PostgreSQL integration result is claimed from that environment.

## Statically validated

- Python lock resolution and complete Python quality/test suite
- API/OpenAPI contract generation and Alembic offline SQL generation
- console lock install, lint, type check, unit tests, and production build
- Terraform formatting, provider initialization, and `terraform validate`
- Bash syntax for both GCE startup scripts
- YAML parsing for all GitHub Actions workflows

## Deferred until Docker is available

Run these in order on a Linux Docker host. Stop at the first failure and retain
the command output as a CI or issue artifact.

```bash
docker version
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env config --quiet
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env build --pull
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env up -d --wait
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:3000/
docker compose -f deploy/compose/lab.yml --profile mock config --quiet
docker compose -f deploy/compose/lab.yml --profile mock up -d --build --wait
docker compose -f deploy/compose/lab.yml exec loadgen crosstalk session run /app/experiments/validation.yaml --root /artifacts
```

The final command performs real local collection against the repository's mock
upstream. It is not a substitute for the Linux offload, tshark agreement, loss
and reordering, sustained-load, or GCP L4/T4 validation gates in the experiment
documents.

## Known deployment boundary

The durable queue, leasing, retries, cancellation, stage registry, and worker
engine are implemented and unit-tested. Production stage adapters that advance
the complete `plan -> collect -> reconstruct -> validate -> features -> attack
-> stats -> report` DAG are not yet wired to worker process entrypoints. The
platform Compose file therefore starts PostgreSQL, migrations, API, and console
only; it intentionally does not advertise nonfunctional worker services.

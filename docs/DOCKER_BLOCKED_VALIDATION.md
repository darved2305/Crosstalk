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
- Python and production Node dependency audits against current advisory data

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

The durable queue, leasing, retries, cancellation and stage registry are wired to
the `crosstalk-worker` entry point. It runs the `plan -> collect -> reconstruct
-> validate` stages per session and the `analyze -> report` stages per
experiment. `tests/test_pipeline_postgres.py` drives that path end to end
against a real PostgreSQL server: API run, queue, worker, artifacts and report.
The platform Compose file adds a `worker` service. That service definition was
validated statically, but no container was run because Docker is unavailable on
this host. The worker's local-dev lab is not packet capture, so the Docker/Linux
capture boundary described above still applies.

# Control-plane runbook

## Start or upgrade

Copy `deploy/compose/platform.env.example` to
`deploy/compose/platform.env`, replace every value, and keep the file outside
version control. Use a URL-safe PostgreSQL password because the Compose file
embeds it in a SQLAlchemy URL. Operator and oracle tokens must be distinct and
at least 32 characters.

```bash
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env up -d --build --wait
curl --fail http://127.0.0.1:8080/readyz
```

The one-shot `migrate` service runs Alembic before the API is eligible to start.
PostgreSQL data lives in the named `postgres-data` volume. Experiment artifacts
remain host-owned under `artifacts/` and are mounted read-only into the API.

## Restart and inspect

```bash
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env ps
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env logs --since=15m postgres migrate api console
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env restart api console
```

API restarts are safe: accepted work is persisted before the 202 response.
Expired worker leases are recoverable through `JobRepository.reap_expired()`;
until the production stage-adapter process is added, do not submit collection
runs expecting automatic stage execution.

## Cancel or retry

Use the authenticated control endpoints rather than editing rows directly:

```bash
curl -X POST -H "Authorization: Bearer $CT_API_OPERATOR_TOKEN" \
  http://127.0.0.1:8080/v1/experiments/EXPERIMENT_ULID/cancel
curl -X POST -H "Authorization: Bearer $CT_API_OPERATOR_TOKEN" \
  http://127.0.0.1:8080/v1/jobs/JOB_ULID/retry
```

Cancellation is cooperative for running work and immediate for queued work.
Retry is accepted only for failed/dead jobs and resets the attempt counter.
The console never bundles an oracle token. An operator may enter it explicitly
for the validation-only emissions view; the browser keeps it in session storage
until that tab is closed or the console is disconnected. The API still enforces
oracle scope on each request.

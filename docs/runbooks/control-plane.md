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
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env logs --since=15m postgres migrate api worker console
docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env restart api console
```

API restarts are safe: accepted work is persisted before the 202 response.
The `worker` service (`crosstalk-worker`) claims jobs from the `collect`,
`attack` and `evaluate` queues and runs the stage handlers in
`crosstalk.orchestration.pipeline`:

| Stage | Scope | Queue | Effect |
|---|---|---|---|
| plan | session | collect | resolves the workload and model for the lab; records notes |
| collect | session | collect | child process runs the load generator through the shaper and capture |
| reconstruct | session | attack | pcapng → TCP/TLS records → request flow map |
| validate | session | evaluate | emission fidelity, tshark/keylog oracles when available → VALID/INVALID/PARTIAL |
| analyze | experiment | evaluate | E0/E1/E2 features, attackers and statistics, or a validation summary |
| report | experiment | evaluate | verifies `report.md`/`summary.json` and writes a hashed `index.json` |

Each stage enqueues its successor with a deterministic stage key, so retries
are idempotent. The last session to reach a terminal state enqueues `analyze`
under the experiment row lock. Outputs are keyed by experiment ULID
(`artifacts/experiments/<ULID>/`), so rerunning one configuration never pools
sessions. The worker reaps expired leases every 15 s. `CT_WORKER_LAB=local-dev`
(default) uses the synthetic upstream and recording relay and is never
reportable; `CT_WORKER_LAB=external` targets the Linux laboratory's shaper and
dumpcap capture supervisor. The Compose worker definition has been validated
statically only, because Docker was not run in this verification.

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

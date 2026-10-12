# Zero-cost Windows demo

This runbook starts the real React console, FastAPI service, PostgreSQL
control plane and production worker without Docker or cloud billing. Dashboard
runs execute every stage (plan → collect → reconstruct → validate → analyze →
report) in the worker's **local-dev lab**: a synthetic mock upstream, the
production TLS shaper and a recording relay with synthetic TCP segmentation.
Sessions therefore end `PARTIAL` and reports are `NOT REPORTABLE`. Linux packet
capture, the tshark oracle and GPU serving are not part of this demo.

## 1. Start an isolated PostgreSQL cluster

Run these commands from the repository root in PowerShell. The cluster accepts
passwordless connections only on the local loopback interface and stores its
files under the ignored `artifacts/` directory.

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$pgData = Join-Path (Resolve-Path '.\artifacts') 'postgres-demo'
if (-not (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) {
  & "$pgBin\initdb.exe" -D $pgData -A trust -U crosstalk --encoding=UTF8 --no-locale
}
& "$pgBin\pg_ctl.exe" -D $pgData -l (Join-Path (Resolve-Path '.\artifacts') 'postgres-demo.log') -o '-p 55432 -h 127.0.0.1' start
& "$pgBin\createdb.exe" -h 127.0.0.1 -p 55432 -U crosstalk crosstalk
```

If `createdb` says the database already exists, continue. Apply the schema:

```powershell
$env:CT_API_DATABASE_URL = 'postgresql+psycopg://crosstalk@127.0.0.1:55432/crosstalk'
uv run alembic upgrade head
```

## 2. Start the API

Keep this terminal open. Port 18080 avoids the Apache service commonly using
port 8080 on this workstation.

```powershell
$env:CT_API_DATABASE_URL = 'postgresql+psycopg://crosstalk@127.0.0.1:55432/crosstalk'
$env:CT_API_OPERATOR_TOKEN = [guid]::NewGuid().ToString('N')
$env:CT_API_ORACLE_TOKEN = [guid]::NewGuid().ToString('N')
$env:CT_API_CODE_COMMIT = git rev-parse HEAD
$env:CT_API_ARTIFACTS_ROOT = (Resolve-Path '.\artifacts').Path
uv run uvicorn 'crosstalk.api.app:create_app' --factory --host 127.0.0.1 --port 18080
```

Record the operator token for the console. Do not commit it.

## 3. Start the worker

In another terminal (same repository root):

```powershell
$env:CT_WORKER_DATABASE_URL = 'postgresql+psycopg://crosstalk@127.0.0.1:55432/crosstalk'
$env:CT_WORKER_ARTIFACTS_ROOT = (Resolve-Path '.rtifacts').Path
$env:CT_WORKER_CODE_COMMIT = git rev-parse HEAD
uv run crosstalk-worker
```

The worker claims jobs with `FOR UPDATE SKIP LOCKED` leases and runs each
collection in a child process. `uv run crosstalk-worker --once` drains every
runnable job and exits, which is useful in scripts. Optional settings:
`CT_WORKER_MOCK_TOKEN_DELAY_MS` (default 1.0) and `CT_WORKER_QUEUES`
(JSON list, default `["collect","attack","evaluate"]`). On Windows, TLS
keylog validation is disabled for local-dev sessions and recorded as a session
note, because CPython's `keylog_filename` aborts under this OpenSSL build.

## 4. Start the console

In a second terminal:

```powershell
Set-Location apps\console
$env:CT_CONSOLE_API_URL = 'http://127.0.0.1:18080'
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://127.0.0.1:3000`, enter the operator token, and create an
experiment. Starting a run writes PostgreSQL experiment, session and job rows.
The worker then executes them, and the console follows the stages until the
report is published. A small E0 configuration (two sessions, `per_class: 6`)
finishes in about a minute.

## 5. Verify the real stack

With both services still running:

```powershell
Set-Location apps\console
$env:CT_E2E_OPERATOR_TOKEN = '<same operator token>'
$env:CT_E2E_EXPERIMENT_ID = '<experiment ULID>'
pnpm exec playwright test tests/real-stack.spec.ts
```

The test checks database readiness, an unauthorized response, authenticated
dashboard data, experiment/session/job rendering, and desktop/mobile layouts.
It writes screenshots to `docs/evidence/`.

## 6. Stop the database

After stopping Vite, the worker and Uvicorn with Ctrl+C:

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$pgData = Join-Path (Resolve-Path '.\artifacts') 'postgres-demo'
& "$pgBin\pg_ctl.exe" -D $pgData stop
```


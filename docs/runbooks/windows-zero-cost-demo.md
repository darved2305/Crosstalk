# Zero-cost Windows demo

This runbook starts the real React console, FastAPI service, and PostgreSQL
control plane without Docker or cloud billing. It does **not** claim that the
scientific worker DAG, Linux packet capture, or GPU serving is running.

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

## 3. Start the console

In a second terminal:

```powershell
Set-Location apps\console
$env:CT_CONSOLE_API_URL = 'http://127.0.0.1:18080'
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://127.0.0.1:3000`, enter the operator token, and create or inspect an
experiment. Creating a run writes real PostgreSQL experiment, session, and job
rows. Jobs remain queued because production stage adapters are not yet wired;
the UI deliberately displays that state rather than inventing measurements.

## 4. Verify the real stack

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

## 5. Stop the database

After stopping Vite and Uvicorn with Ctrl+C:

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$pgData = Join-Path (Resolve-Path '.\artifacts') 'postgres-demo'
& "$pgBin\pg_ctl.exe" -D $pgData stop
```


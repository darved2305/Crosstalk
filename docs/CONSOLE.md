# React console

The console is a React 19 + strict TypeScript + Vite client using React Router,
TanStack Query, Zod response validation, and the existing FastAPI `/v1` API.
It has an experiment grid/list with deep-linkable search, state filter and view
(`/?q=…&state=…&view=list`), experiment creation (validation or E0 calibration),
run/cancel/retry controls, a live per-session stage pipeline
(plan → collect → reconstruct → validate) and experiment stages
(analyze → report), an activity timeline from pipeline events, a NOT
REPORTABLE banner with validation reasons, an inline report preview, a trace
explorer, the E0–E2 results table, the leakage/cost frontier, and a rendered
markdown report view with download.

## Native development

From `apps/console`, run `pnpm install --frozen-lockfile`, then `pnpm dev`.
The site is at `http://127.0.0.1:3000`. Vite proxies `/v1` to
`CT_CONSOLE_API_URL`, which defaults to `http://127.0.0.1:8080`. Run the API
separately with `CT_API_OPERATOR_TOKEN`, `CT_API_ORACLE_TOKEN`, and the normal
database/artifact configuration. This setup does not start Docker.

Enter the operator token in the UI. It stays in that browser tab's session
storage and is sent as a bearer token on API requests. The oracle token is
entered separately only to request application emissions; backend scope checks
remain authoritative. Disconnect removes both tokens and cached query data.
Deploy the static `dist/` output behind a same-origin `/v1` reverse proxy. The
console Dockerfile contains an nginx configuration for that layout, but it was
only statically updated in this migration and was not run locally.

## Verification

`pnpm check` regenerates types from `docs/openapi.json`, lints, typechecks,
runs Vitest, and builds Vite. `pnpm test:e2e` runs the Playwright suite. The
fixture specs (`tests/console.spec.ts`) intercept `/v1` with test-only route
fixtures and need no backend; the production app contains no sample records or
mock transport. The real-stack specs are opt-in and skip without
`CT_E2E_OPERATOR_TOKEN`:

- `tests/pipeline-live.spec.ts` needs `CT_E2E_BASE_URL` (a running console that
  proxies to a live API and worker, e.g. `http://127.0.0.1:3010`). It creates an
  E0 experiment through the form, starts a run, waits up to three minutes for
  the report to appear without reloading, and asserts NOT REPORTABLE, stage
  states, activity events, and that polling stops. `CT_E2E_SCREENSHOT_DIR`
  additionally captures evidence screenshots.
- `tests/real-stack.spec.ts` and `tests/campaign.spec.ts` call the API directly
  at `CT_E2E_API_URL` (default `http://127.0.0.1:18080`). Note that
  `campaign.spec.ts` overwrites `docs/evidence/campaign/screenshots/`.

Set `CT_PLAYWRIGHT_CHROMIUM` to a local Chromium executable when the
Playwright-pinned browser build is not installed.

## Behaviour notes

- Polling: the experiment page refetches every 2 s while the experiment is
  `running` or any job is `queued`/`running`, and stops once it is terminal.
  The API reports a created-but-never-run experiment as `running`; the console
  shows it as "not started" and does not poll it.
- Controls: Start run sends a fresh `Idempotency-Key` (reused only if the
  previous attempt failed in transport), is disabled while a run is live, and
  Cancel requires an inline confirmation. Failed/dead jobs get a Retry button.
  Errors show the RFC 9457 `title (status): detail`.
- Reports: rendered with `react-markdown` + `remark-gfm`; raw HTML is skipped
  and unsafe URLs are dropped. Report figures are shown as captioned
  placeholders because the API does not serve report images.
- Trace explorer: experiment and session come from selectors; the request ID
  must be pasted because the API has no request-listing endpoint (recently
  opened IDs are remembered per session in local storage).
- Charts: the frontier plots ρ with 95% CI whiskers against a labelled cost
  axis with units, a ρ = 1 reference line and a zero-cost line, and lists every
  row (including undefined ρ) in a table. The trace plot shows signed bytes
  (server→client positive) against time since the first record in ms; for
  downsampled bins it draws the per-direction extreme.

## Verification on 2026-10-11

- `pnpm check`: passed; 4 Vitest tests and Vite production build.
- `pnpm test:e2e`: 3 Playwright tests passed at 1504×1128 and 390×844 using
  a cached local Chromium executable. Routes in these tests use fixtures.
- `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright`:
  passed (Pyright: 0 errors and warnings).
- `uv run pytest -q`: 182 passed after fixing the asynchronous emission-log
  collection race. An earlier run had 180 passed and one wire-fidelity failure;
  the focused test and the final full suite passed after the fix.
- Docker execution, a live PostgreSQL-backed browser session, actual packet
  capture, GPU serving, cloud deployment, and scientific E0–E2 collection were
  not run here. The recording mentioned in the UI request was not present as a
  local file; visual inspection used the four identical cropped stills shown
  in the conversation and Playwright screenshots.

## Live local-stack verification on 2026-10-12

The console was subsequently exercised without Docker against FastAPI and an
isolated native PostgreSQL 18 cluster. This found and fixed two fixture-hidden
contract defects: persistence-only columns caused strict response validation
failures, and the console rejected valid numeric-offset timestamps emitted by
FastAPI. The opt-in real-stack Playwright test now checks readiness,
unauthorized access, authenticated experiment/session/job data, and desktop and
mobile rendering. See [`runbooks/windows-zero-cost-demo.md`](runbooks/windows-zero-cost-demo.md)
for reproduction and [`evidence/README.md`](evidence/README.md) for provenance.

That verification stopped at queued plan jobs; the worker-driven pipeline was
verified later the same day (below). Linux capture, GPU inference, and
reportable E0–E2 collection remain pending; the UI does not synthesize those
results.

## Live pipeline verification on 2026-10-12 (console v2)

Against FastAPI on `127.0.0.1:18090`, native PostgreSQL, the production
worker and Vite on `127.0.0.1:3010` (no Docker, no request interception):

- `pnpm check`: passed — lint (0 problems), typecheck, 40 Vitest tests in 9
  files, production build.
- `pnpm exec playwright test` without a token: 8 fixture tests passed, 5
  opt-in real-stack tests skipped.
- `CT_E2E_BASE_URL=http://127.0.0.1:3010 pnpm exec playwright test
  tests/pipeline-live.spec.ts` with the operator token: 2 passed. An E0
  experiment created in the UI ran plan → collect → reconstruct → validate for
  two sessions and analyze → report in about 45 s; the page progressed and
  showed the report without a reload, then stopped polling.
- No horizontal page scroll at 1504×1128, 1280×800, 768×1024 and 390×844
  (asserted in the fixture suite and checked on the live pages).
- Screenshots and their provenance: [`evidence/console-v2/`](evidence/console-v2/README.md).

Backend gaps found: no endpoint lists a session's request IDs (the trace
explorer needs one, e.g. `GET /v1/sessions/{id}/requests` returning request
ID, class, arm and record count from `flow_map`/`plan`); report figures under
`experiments/<id>/figures/` are not served (e.g.
`GET /v1/experiments/{id}/report/figures/{name}`); a never-run experiment is
reported as `running`; and cancelling an experiment while a second run is
queued also flags the first run's completed sessions `cancel_requested` and
turns the whole experiment `cancelled`, although that first run had already
published a report.

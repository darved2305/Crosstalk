# React console

The console is a React 19 + strict TypeScript + Vite client using React Router,
TanStack Query, Zod response validation, and the existing FastAPI `/v1` API.
It has experiment grid/list views, state filters, experiment creation, run and
cancel controls, job retry, session inspection, trace explorer, E0–E2 result
table, leakage/cost frontier, and report viewing/downloading.

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
runs Vitest, and builds Vite. `pnpm test:e2e` runs Playwright interactions and
saves desktop and mobile screenshots under the ignored `test-results/` folder.
The browser tests use test-only route fixtures; the production app contains no
sample records or mock transport. No pixel comparison is automated because the
provided reference consisted of four identical cropped stills, not a local
recording file or full-page screenshots.

## Current backend boundary

Experiment configuration and mutations use real `/v1` endpoints. The report
view reads `experiments/<name>/report.md` through an authenticated API route.
No report appears until that artifact exists. The existing production worker
stage adapters are still incomplete, so a newly queued run is not guaranteed
to produce measurements on this host. E0–E2 result panels show pending/error
states when result artifacts are absent; they never synthesize measurements.
The current frontier artifact contract has no CPU value even though the API
accepts `cost=cpu`, so the CPU plot explicitly says no CPU cost data is exposed.

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

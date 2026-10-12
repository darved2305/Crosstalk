# Verification evidence — 2026-10-12

These files are evidence from an actual local stack, not cloud-deployment
screenshots and not scientific E0–E2 results.

## Retained E0/E1/E2 campaign

The report is assembled in [`../PROJECT_REPORT.md`](../PROJECT_REPORT.md).
`campaign/` contains generated reports, JSON summaries, figures, exact configs,
and ten actual UI screenshots. Desktop captures use 1504×1128 and the mobile
capture uses 390×844. Every screenshot was captured against the running API and
PostgreSQL/Parquet stores, with no intercepted browser responses.

Executed command:

`uv run pytest -q tests/test_e2e_e0.py tests/test_e2e_e1.py tests/test_e2e_e2.py
--basetemp=artifacts/report-campaign-20261012`

Result: 4 passed in 348.64 s; six sessions, 859 completed requests, zero failed
requests, and 552 evaluation rows. `campaign/campaign.json` records session IDs,
artifact hashes, import time, and measured classifier results. The reports
preserve the source commit and dirty-tree state recorded during collection.

The upstream was synthetic SSE. The recorder captured actual TLS ciphertext
and generated synthetic TCP segmentation. All sessions remain `PARTIAL` and all
reports are marked `NOT REPORTABLE`; these artifacts demonstrate engineering
execution and do not establish research conclusions on production LLM traffic.

Browser verification with `CT_E2E_OPERATOR_TOKEN` and `CT_E2E_EXPERIMENT_ID`
configured: `pnpm test:e2e` — 6 passed. Three tests use fixtures; three use the
real local stack, including campaign rendering and durable API mutations.
The campaign test also verifies operator access to oracle emissions is denied
with HTTP 403, and actual recorded packet and TLS trace rendering.

Final Windows verification: `uv run pytest -q` — **184 passed in 282.67 s**;
`pnpm test` — **10 passed**; `pnpm lint`, `pnpm typecheck`, and `pnpm build`
passed. `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pyright`
passed; `uv run lint-imports` kept all seven architecture contracts.

Hosted Ubuntu CI passed all five jobs for commit `09d2e5b`:
<https://github.com/darved2305/Crosstalk/actions/runs/38227854706>.
The hosted Python run passed 184 tests with 81.56% coverage. Its three browser
tests use fixtures; the three opt-in tests for the real stack are skipped there.
GitHub billing usage was inaccessible to the current token; total Actions
charges are not asserted. No GCP resources or billing changes were made.

`runtime-linux-20261012.json` records the actual 20-second Linux/uvloop runtime
invocation with three repetitions and a 30-second soak. The runtime gate
**failed**: at 256 streams timer p99 was 9 ms, relay added p99 was 35.44 ms with
156 errors in each repetition; slow-client isolation also exceeded its target.
Full analysis, exact command, and limitations are in the project report.

`gcp-audit.json` contains actual authenticated read-only Google Cloud outputs.
It proves billing was disabled. The Compute listing contains a billing-required
warning; its empty list is not a complete VM inventory. The listed historical
`billing-killswitch` service is not CrossTalk. No cloud application was deployed
or tested. Browser discovery returned no available surfaces, so Google Cloud
console screenshots could not be captured.

- `local-stack-desktop.png`: Chromium at 1504×1128 showing the Vite console
  populated through its `/v1` proxy from FastAPI and the isolated PostgreSQL 18
  database. The experiment has two real session rows and two queued plan jobs.
- `local-stack-mobile.png`: Chromium at 390×844 showing the same experiment,
  session, defense, and job state at the mobile viewport.

Provenance:

- GCP project inspected: `inspired-campus-436516-k4` (`db-opt`).
- `gcloud billing projects describe inspired-campus-436516-k4` returned
  `billingEnabled: false`; no billing was enabled and no cloud resource was
  created.
- Google Cloud documents that an active billing account is required even for
  Free Tier usage: <https://docs.cloud.google.com/free/docs/free-cloud-features>.
- Local API: `127.0.0.1:18080`; Vite: `127.0.0.1:3000`; PostgreSQL:
  `127.0.0.1:55432`.
- Experiment: `01M4QHX1AJAS33W53TMQ7FDGE0`, created from
  `experiments/validation.yaml` through the real API.
- Browser command: `pnpm exec playwright test tests/real-stack.spec.ts` with
  `CT_E2E_OPERATOR_TOKEN` and `CT_E2E_EXPERIMENT_ID` set. Result: 1 passed.
- The test also verified `/readyz` returned 200 and an unauthenticated
  `/v1/experiments` request returned RFC 9457-style HTTP 401 problem JSON.
- Tokens are stored only in process/session memory and are not present in these
  images or committed files.
- Bounded runtime command: `uv run python bench/runtime/run.py --duration 0.4
  --output artifacts/bench/runtime-windows-20261012.json`. The measured results,
  raw artifact hash, and explicit non-reportability statement are in
  [`../../bench/RESULTS.md`](../../bench/RESULTS.md).

Limitations: the queued jobs prove the control plane, not worker execution.
Windows cannot qualify the Linux/uvloop capture runtime gate. No GCP service,
public backend URL, GPU, packet capture, or reportable leakage result is claimed.


# Verification evidence — 2026-10-12

These files are evidence from an actual local stack, not cloud-deployment
screenshots and not scientific E0–E2 results.

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


# Cloud Shell run — 2026-10-15

The Docker validation ledger ([`../../DOCKER_BLOCKED_VALIDATION.md`](../../DOCKER_BLOCKED_VALIDATION.md))
executed on Google Cloud Shell, account `darshanved03@gmail.com`. No billing
account was used and no GCP resources were created. Cloud Shell is a Linux VM
(Docker 29.0.2, 2 vCPU, 7 GB RAM, region asia-southeast1). Code: commit `efb2ea5`
plus the fixes listed below, uploaded as an archive and committed into a local
snapshot repository so `git_dirty=false` could be recorded.

The upstream is the repository's mock (synthetic SSE), not vLLM on a GPU.
Nothing here is a reportable leakage result.

## What ran

| Step | Result | Screenshot |
|---|---|---|
| `docker compose -f platform.yml build --pull` and `up -d --wait` | postgres, migrate, api, worker, console built and healthy | 01 |
| `curl /healthz`, `/readyz`, console on `:3000` | failed first (ports unpublished), passed after fix 1 | 01, 02 |
| `docker compose -f lab.yml --profile mock up -d --build --wait` | shaper, netinit, loadgen, capture, mock-upstream built; netinit failed until fix 3 | 03, 04 |
| `crosstalk lab provenance` | written after fixes 3–4 | 05 |
| `crosstalk session run experiments/validation.yaml` (dumpcap capture) | 2 sessions × 200 requests, 0 failed, pcapng 1.66 / 1.76 MB | — |
| `crosstalk reconstruct run` + `validate` in the capture container | see below | 06, 07 |
| `POST /v1/experiments` + `POST /runs` → worker | collect → reconstruct → validate → analyze → report all succeeded after fixes 2, 5, 6; experiment `partial` (local-dev lab is non-reportable by construction) | 08, 09 |
| Console through Cloud Shell Web Preview | dashboard, experiment, pipeline, report, trace explorer, oracle gate | 10–16 |

### Packet-capture validation (V1–V5 on Linux)

NIC offloads on the loadgen `eth0`: TSO, GSO, GRO all `off`.

| Session | Concurrency | Records ours / tshark | Emission fidelity | Incomplete flows | Status |
|---|---|---|---|---|---|
| `01M4Z24Q6R7VCKJ75F47QZYBT9` | 1 | 5,593 / 5,593 (exact) | 3,394 / 3,400 = 0.99824 | 1 | **INVALID** |
| `01M4Z257QXYHED2HHFA36QV0WV` | 16 | 5,600 / 5,600 (exact) | 3,400 / 3,400 = 1.0 | 0 | **VALID** |

The C=1 session's three failure reasons all come from one incomplete TCP flow
(6 missing emissions). Its cause has not been investigated yet.

## Defects found and fixed

1. `platform.yml`: api and console sat only on an `internal: true` network, so
   Docker published no host ports. They now also join a `published` network.
2. `platform.yml`: the worker ran as uid 10001 and could not write the
   host-owned `artifacts/` bind mount. Its user is now `CT_WORKER_USER`
   (default `10001:10001`); the cloud scripts set it to the host user.
3. `lab.yml`: `shaper-netinit` and `impairment` run as root with all
   capabilities dropped, so they could not write host-owned `artifacts/lab`.
   They now use the host group (`0:${CT_GID}`), like `capture`.
4. `common/artifacts.py`: atomic writes produced `0600` files (mkstemp), so the
   root-written offload snapshot was unreadable by the host user. Files now get
   the mode `open()` would give under the process umask.
5. `loadgen/session.py`: provenance shelled out to `git`, which the worker
   image does not have. Without git it now records `CT_CODE_COMMIT` with
   `git_dirty=true`.
6. Worker image lacked the `ml` extra (`numpy`, ...) and `matplotlib` existed
   only in the dev group, so analyze/report could not run in production. The
   API Dockerfile takes `UV_EXTRAS`; the worker builds with `--extra ml`, which
   now includes matplotlib.
7. `deploy/cloud-vm/validate.sh`: a leaked `umask 077` created `artifacts/` as
   `0700`. The umask is now scoped to writing the secrets file.
8. Console: Cloud Shell Web Preview (like IAP) claims any request carrying an
   `Authorization` header for its own login (503 + redirect to
   `cloudshell/jwt`). The console now sends `X-Crosstalk-Authorization`;
   nginx and the Vite dev proxy map it back to `Authorization` for the API.
9. `contracts/parquet.py`, `capture/reconstruct.py`: Parquet artifacts were also
   written `0600` via mkstemp, so the API (uid 10001) could not read observer
   tables the worker wrote (trace explorer 422, screenshot 14). They now use the
   same umask-derived mode as fix 4.

## Console in the cloud (screenshots 10–16)

The Vite console ran in the `console` container and was opened through Cloud
Shell Web Preview on port 3000. Experiment `01M4Z84MRH7SM3A0BFE6FGKZFX`, created
through the API and executed by the worker: 2 sessions × 40 requests, 0
failed, 10/10 jobs succeeded, emission fidelity 1.0000 in both sessions, and
an explicit NOT REPORTABLE banner (local-dev recording relay, mock upstream,
no tshark on the worker host, provenance from `CT_CODE_COMMIT` marked dirty).

After fix 9, the trace explorer rendered request `01M4Z8PSFRC9PC2QN75V2C58C1`
(experiment `01M4Z8P8KAA2EPJKQDPR6Q2R8X`): 28 TLS records, 24 server-to-client
and 4 client-to-server (15). The oracle emissions panel stays locked behind a
separate oracle token; the operator token cannot read it (16).

Cloud Shell discarded every container, image and the PostgreSQL volume once
during the session; the stack was rebuilt from the same files.

## Not covered

vLLM/GPU serving, netem loss/reorder sessions, sustained load, the runtime
performance gate, and cloud VM/Terraform deployment.

# IMPLEMENTATION_PLAN.md — Crosstalk

**The engineering authority.** Research rationale: `PRODUCT.md`. Evidence: `RESEARCH.md` (cited as `RESEARCH §n` / `[R#]`).

Version 2.0 · 19 September 2026 · supersedes v1.0 (the 14-phase roadmap and all Rust/Cargo content are removed).

This plan is written so that coding agents can build Crosstalk end to end **without inventing architecture**. Where a decision depends on an empirical result, it is a **gate** with an exact benchmark, threshold and fallback. Only one P0 gate remains open: the Day-1 runtime confirmation (§3.2).

---

## 0. Rules for implementers

1. **Build the scientific instrument first.** The local lab, capture, reconstruction and attacks are production code from Day 1. Orchestration (Day 7), cloud (Day 8) and UI (Day 9) wrap a working instrument.
2. **Language constraint (non-negotiable).** Crosstalk application code is written in **Python 3.13** and **TypeScript**, and in Java 21 (no Spring) only if the Day-1 gate forces it. **No Rust, C, C++ or Spring Boot** is written for this project. Mature external software written in those languages (vLLM, OpenSSL, dumpcap/tshark, PostgreSQL, the Linux kernel) is deployed, never extended with native code.
3. **Never assume boundaries.** A `write()` is not a TLS record, and a record is not a packet. Every boundary claim is measured and validated (§8–§10).
4. **Oracle data never reaches the attacker.** Attack code reads only observer-view tables and labels (§14.3). The build enforces it.
5. **Every reportable number has a manifest:** config hash, code commit, image digests, model revision, workload version, seeds, environment, capture configuration (§14.4).
6. **No per-emission logging or tracing in the shaper hot path.** Emissions go to a buffered oracle log. Logs and spans are per stream only.
7. **A task is done** when code, tests, docs and (for measurements) a committed result file exist, and CI is green.

---

## 1. Engineering principles

- **Measure, don't simulate, when measurement is possible.** Simulated defense numbers exist only in the explicit simulated-vs-deployed comparison (P1), labelled `simulated=true`.
- **Simplest adequate component.** A modular Python package plus a few containers. No microservice per function, no Kafka/Redis/Pub/Sub/BigQuery/Temporal/service mesh.
- **Idempotent, resumable stages** keyed by `(stage, input hashes, config hash)`. Outputs are written to a temp dir and atomically renamed, with a sha256 recorded.
- **Bounded everything:** queues, buffers, retries (exponential backoff + full jitter), timeouts at every I/O boundary, memory caps per stream.
- **Explicit failure states.** A session with capture drops, incomplete flows or failed validation is `INVALID` and cannot feed a reportable result.
- **Typed contracts:** Pydantic v2 models for configs and API, pyarrow schemas for every Parquet table, JSON Schema exported for configs, OpenAPI generated for the console.

---

## 2. Selected stack

| Layer | Choice | Version policy |
|---|---|---|
| Language (backend, data path, ML) | Python **3.13** (`requires-python = ">=3.13,<3.14"`) | Exact patch pinned via `.python-version` and uv |
| Packaging / env | **uv** workspace, `uv.lock` committed | `uv sync --locked` in CI |
| Lint / format / types | **Ruff** (lint + format), **Pyright** strict on `src/`, **import-linter** contracts | Pinned in the dev dependency group |
| Async runtime | `asyncio` + **uvloop 0.22.x** | Default asyncio loop is **not** allowed in the shaper (§3.3 evidence) |
| TLS | Python `ssl` (OpenSSL 3.x from the base image), TLS 1.3 only | OpenSSL version recorded in manifests |
| HTTP/1.1 (shaper server side, loadgen client) | **h11 0.16** (sans-IO), own framing | — |
| Upstream client (shaper → vLLM) | **aiohttp 3.14** `ClientSession` | Inside the backend network only; not in the measured leg |
| Serving engine | **vLLM 0.29.0** (`vllm/vllm-openai` image pinned by digest) | Model and revision pinned per campaign |
| Capture | **dumpcap** (Wireshark package) as an external process | Version recorded |
| Validation oracle | **tshark** (same package) | Version recorded |
| PCAP/TCP/TLS parsing | **Own Python** (`crosstalk.capture`): pcap + pcapng reader, TCP reassembly, TLS record parser | dpkt/pyshark rejected (stale, slow) — RESEARCH §11.1 |
| Dataframes / storage / query | **Polars 1.44**, **Parquet** (pyarrow 25), **DuckDB 1.5** | — |
| ML | **scikit-learn 1.9** (LR, CV utilities), **LightGBM 4.7** | PyTorch only for the P2 CNN |
| Stats | NumPy/SciPy + own cluster-bootstrap module | — |
| API | **FastAPI** + uvicorn | Day 7 |
| Transactional state | **PostgreSQL 16** (psycopg 3, SQLAlchemy 2 Core, Alembic) | Day 7; also serves as the job queue |
| Frontend | **Next.js + TypeScript (strict)**, pnpm, ESLint, zod, openapi-typescript, uPlot + SVG | Day 9; current stable Next major pinned |
| Containers | Docker (BuildKit), Compose v2 | Base images pinned by digest |
| IaC | **Terraform** (GCP provider), remote state in GCS | Day 8 |
| CI/CD | **GitHub Actions**; GCP via Workload Identity Federation; GHCR/Artifact Registry | Day 1 (CI), Day 8 (cloud) |
| Cloud | **GCP Compute Engine** (1× L4 GPU VM + 1 client VM), GCS, Artifact Registry, IAM, Budgets | Day 8; GKE is P2 |

---

## 3. Language and runtime decisions

### 3.1 Language decision table

| Component | Candidates | Selected | Why | Risk | Validation benchmark |
|---|---|---|---|---|---|
| **Shaper (TLS-terminating SSE proxy + policies)** | Python asyncio+uvloop; Node.js 24; Java 21+Netty; mature proxy (nginx/OpenResty, Envoy+ext_proc, HAProxy) | **Python 3.13 + uvloop**, own TLS termination | Deterministic record boundaries: 1 `write()` ≤ 16 KiB → 1 record, even back-to-back. Timer lateness and relay latency in Node's class at the target load (Day-0, RESEARCH §11.3). Same language as the analysis pipeline. Mature proxies either cannot schedule timing without Lua rewrites or put an opaque TLS writer and an extra gRPC hop in the measured path (RESEARCH §11.2) | Tail latency above ~5k emissions/s per process; asyncio task-reference and GC pitfalls | **Day-1 gate G1–G6** (§3.2) on the lab host |
| **Packet capture** | dumpcap; tcpdump; Scapy/pypcap sniffing; custom AF_PACKET | **dumpcap** (external process) | Mature libpcap engine; pcapng with interface statistics (drop counters travel with the artifact); same toolchain as the tshark oracle | Needs `NET_RAW`/`NET_ADMIN` in the capture container | Zero kernel/ifdrop at target load; completeness gate (§8) |
| **PCAP → TCP → TLS parser** | tshark field export; pyshark; Scapy; dpkt; own Python | **Own Python** (`struct`/`memoryview`), tshark as the validation oracle | Full control of reassembly semantics and timestamps; no stale dependency; subprocess-per-packet avoided | Reassembly edge cases (retransmission, overlap, out-of-order) | 100% record agreement with tshark on the validation corpus; ≥ 200k packets/s (§27) |
| **Load generator** | Python asyncio (h11); httpx; aiohttp; k6; Locust | **Python asyncio + uvloop + h11** | Connection-per-request control, local-port capture for the flow join, per-read timestamps, seeded plans | Client-side CPU at high concurrency | Client lateness < 1 ms p99 at C=64 (self-check) |
| **Control API** | FastAPI; Node/Express | **FastAPI** | Pydantic contracts shared with the pipeline; OpenAPI → TS client | Low | Contract tests, p95 < 200 ms for read endpoints |
| **ML pipeline** | Python (sklearn/LightGBM/XGBoost) | **Python: scikit-learn + LightGBM** | Standard; LightGBM matches Whisper Leak's strongest tabular baseline | Under-tuned adaptive attacker | Nested-CV budget parity; synthetic injected-signal test |
| **Orchestration workers** | Postgres `SKIP LOCKED` queue; Celery+Redis; Pub/Sub; Temporal | **Python workers on a Postgres job table** | One durable store for state and queue; exactly the concurrency we need | Lease/heartbeat bugs | Kill-and-resume tests (§26) |
| **Frontend** | Next.js/TS; plain React | **Next.js + TypeScript** | User strength; four screens only | Scope creep | Playwright smoke on 4 screens |
| **Storage / query** | Parquet+DuckDB; Postgres; BigQuery | **Parquet + DuckDB** (+ Postgres for state only) | Experimental data is append-only columnar; DuckDB queries it in place, locally and from GCS | Very large sessions | DuckDB query on a 10M-record session < 5 s |
| **Deployment** | Docker Compose; GCE; GKE Standard; GKE Autopilot | **Docker Compose (lab), GCE + Terraform (cloud)**; GKE Standard P2 | Fewest hidden network layers; NIC offload control; reproducible (§20) | Less orchestration automation | Terraform apply/destroy from empty; campaign reproducible |

### 3.2 Day-1 runtime gate (the only open P0 decision)

- **Purpose:** confirm Python + uvloop on the actual lab host (Linux or WSL2 with Docker), not only on the Day-0 laptop run.
- **Candidates:** (1) Python 3.13 + uvloop, (2) Node.js 24, (3) Java 21 + Netty (measured only if 1 and 2 both fail).
- **Harness:** `bench/runtime/`, rebuilt from the Day-0 probe design in RESEARCH §11.3. Every benchmark runs 3 repetitions, and the decision uses the median repetition.

| Gate | Benchmark | Pass threshold |
|---|---|---|
| **G1 record fidelity** | Instrumented TLS client parses record headers from raw ciphertext. Server writes 50 SSE events (a) spaced 5 ms, (b) back-to-back in one loop iteration, (c) to a slow reader | ≥ 99.99% of emissions ≤ 16 KiB become exactly one record of length `plaintext + 17` in (a) and (b). In (c), no emission is split or merged by the runtime; merging happens only if the policy decides it |
| **G2 timer lateness** | Per-stream absolute-deadline ticks, T = 20 ms, 20 s, N ∈ {1, 64, 256} concurrent TLS streams, one ~200-B record per tick | N=64: p99 ≤ 2.5 ms and p99.9 ≤ 6 ms. N=256: p99 ≤ 5 ms and p99.9 ≤ 12 ms |
| **G3 relay latency** | Mock OpenAI-compatible upstream (25 ms/event) → shaper D0 → TLS client; compare to direct-to-upstream | Added latency Δp99 ≤ 2 ms at N=64. At N=256, no errors and Δp99 ≤ 8 ms |
| **G4 resources** | Same as G2 at N=256 | ≤ 1.0 core (CPU s per wall s) and RSS ≤ 256 MB; RSS growth ≤ 10% over a 30-min soak at N=64 |
| **G5 backpressure** | 1 slow client reading 1 KiB/s plus 63 normal clients | Per-stream buffered bytes ≤ cap (1 MiB); slow stream aborted at the stall timeout; normal streams' G3 unaffected (Δp99 change ≤ 1 ms) |
| **G6 fidelity** | Property test: 10,000 random upstream streams × random TCP chunking × each policy | Decoded content byte-identical; zero spurious chunks in official SDKs |

**Decision rule:**
1. If Python+uvloop passes G1–G6, select it (expected, per Day-0 evidence).
2. Otherwise, if Node passes all gates with an explicit emission serializer (one `socket.write` per emission, never in the same tick, and only when `writableNeedDrain` is false), select Node for the shaper only.
3. Otherwise, build the shaper in Java 21 + Netty (`SslHandler`, explicit `writeAndFlush` per emission, epoll transport), isolated behind the same HTTP/TLS contract and emission-log schema.

**Scale-out escape hatch (any runtime):** N shaper processes sharing port 8443 via `SO_REUSEPORT`. Required before E2 runs above the load validated in G2/G3.

**Fallback isolation:** a runtime change touches only `src/crosstalk/shaper/` (or a separate `services/shaper-node/`). The HTTP contract, the policy semantics (§12) and the emission-log schema (§14) are fixed and tested by language-agnostic black-box tests in `tests/shaper_contract/`.

### 3.3 Day-0 evidence (indicative; laptop, WSL2 kernel 6.6, 10 vCPU; full table in RESEARCH §11.3)

- Python `write()` of one chunk: 50 events → 50 records, spaced or back-to-back. `writelines([size, payload, CRLF])` → 150 records. 20 KB events split at 16,384 plaintext bytes.
- Node `res.write()` spaced: 1 record per event, but the response headers merged into the first event's record. 50 same-tick writes became **1 record of 9,181 bytes**.
- TLS 1.3 session tickets: two records of ~250–282 B after the handshake; `num_tickets=0` removes them.
- Timer lateness p99 / p99.9:

  | Streams | uvloop | Node | asyncio |
  |---|---|---|---|
  | 64 | 1.60 / 2.37 ms | 1.54 / 2.19 ms | 1.89 / 4.90 ms |
  | 256 | 3.18 / 7.03 ms | 4.32 / 9.13 ms | 8.57 / 24.5 ms |

  **uvloop is mandatory.** At 256 streams (12.8k emissions/s) uvloop used 0.60 of a core.
- Passthrough relay Δp99 vs direct (median of 2 repetitions):
  - N=64: +0.9 ms for all three runtimes.
  - N=256: Python +4.2–4.4 ms, Node +8.4 ms; p99.9 reaches 13–20 ms in every runtime.

  Hence the multi-process escape hatch and the E2 load cap.
- Against the §3.2 thresholds, Day-0 Python+uvloop passes G1(a,b), G2, G3 and the CPU/RSS part of G4. **Not yet tested:** G1(c) slow reader, the G4 soak, G5 and G6. Node fails G1(b) without serialization and is borderline on G3 at N=256. The Day-1 run on the lab host is still required.

---

## 4. Architecture decision records (to be committed on Day 1 under `docs/adr/`)

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Three observation layers (emission / record / packet); primary features on TLS records reconstructed from TCP streams | Accepted |
| ADR-002 | Baseline protocol H1-TLS13-1req: HTTP/1.1, TLS 1.3, one request per connection, `TCP_NODELAY`, `num_tickets=0`, no resumption/0-RTT | Accepted |
| ADR-003 | Shaper runtime: Python 3.13 + uvloop, own TLS termination (Node, then Java/Netty, as fallbacks) | Accepted, pending Day-1 gate |
| ADR-004 | Capture with dumpcap to pcapng in the client network namespace; offloads disabled and recorded; tshark as the validation oracle | Accepted |
| ADR-005 | Own pcap/pcapng + TCP + TLS parser in Python; no dpkt/pyshark/Scapy in production paths | Accepted |
| ADR-006 | Defense arms D0–D4 (+D1r P1); one policy per request, selected in lab mode by a fixed-width header; arms randomized within sessions | Accepted |
| ADR-007 | Padding and cover traffic use SSE comment lines only (deployment-compatible); `data:` dummies are forbidden outside experimental arms | Accepted |
| ADR-008 | Oracle/observer data separation enforced by directory layout, import-linter, loader guards and (cloud) IAM | Accepted |
| ADR-009 | Evaluation: prompt-family-disjoint nested CV; static vs adaptive on identical held-out families; paired cluster bootstrap; Holm on primary comparisons | Accepted |
| ADR-010 | Storage: Parquet + DuckDB for data; PostgreSQL for orchestration state and job queue only | Accepted |
| ADR-011 | Cloud baseline on Compute Engine (GPU VM + client VM) via Terraform; GKE deferred to P2 | Accepted |
| ADR-012 | No per-emission logging/tracing in the shaper; oracle emission log is buffered and flushed off the hot path | Accepted |
| ADR-013 | Local lab uses real vLLM on the workstation GPU with a small model; the cloud L4 runs the ~7B model | Accepted, pending Day-1 lab smoke test |

---

## 5. Repository layout

```text
crosstalk/                              # repository root
├── README.md
├── pyproject.toml                      # uv workspace root + tool config (ruff, pyright, pytest, importlinter)
├── uv.lock
├── .python-version                     # 3.13.x
├── Makefile                            # thin wrappers over the CLI; no logic
├── src/crosstalk/                      # single Python package, strict internal boundaries
│   ├── __main__.py                     # `python -m crosstalk` → Typer CLI
│   ├── cli/                            # typer apps: lab, session, capture, reconstruct, features, attack, stats, report, bench, worker, api
│   ├── common/                         # settings (pydantic-settings), logging (structlog JSON), ids (ULID), errors, clocks, hashing, atomic IO
│   ├── contracts/                      # Pydantic models + pyarrow schemas + JSON Schema export (single source of truth)
│   │   ├── config.py                   # ExperimentConfig, SessionPlan, PolicyConfig, CaptureConfig
│   │   ├── tables.py                   # pyarrow schemas: packets, records, flows, flow_map, emissions, requests, client_events, labels, features, predictions, evaluations
│   │   └── manifest.py                 # RunManifest / provenance
│   ├── workloads/                      # corpus builder, tokenizer accounting, regimes (NAT/MATCH-P/MATCH-PR), session planner
│   ├── shaper/                         # data plane (imports only common, contracts)
│   │   ├── server.py                   # TLS listener, connection protocol, graceful shutdown
│   │   ├── http1.py                    # h11 request parsing, response framing helpers
│   │   ├── upstream.py                 # aiohttp client pool to vLLM, timeouts
│   │   ├── sse.py                      # incremental WHATWG-conformant SSE parser
│   │   ├── framer.py                   # chunk framing, exact-size padding, merge, dummy blocks
│   │   ├── emitter.py                  # per-stream scheduler: timers, backpressure, emission, oracle logging
│   │   ├── policies/                   # base.py, passthrough.py, bucket_pad.py, window_batch.py, constant_rate.py, hybrid.py, random_pad.py
│   │   ├── accounting.py               # buffered emission log writer, per-stream stats
│   │   └── admin.py                    # /healthz /readyz /metrics /policies (aiohttp.web on admin port)
│   ├── loadgen/                        # session executor, TLS client, SSE client, capture control client, ground-truth writers
│   ├── capture/                        # dumpcap supervisor, offload control/verification, pcap/pcapng reader, tcp.py, tls.py, reconstruct.py, validate_tshark.py
│   ├── traces/                         # loaders: observer.py (packets/records/flows) and oracle.py (emissions/client_events) — separate modules
│   ├── features/                       # r1_summary.py, r2_cumul.py, r3_sequence.py (P2), packet variants
│   ├── attacks/                        # models.py, splits.py, protocols.py (static/adaptive/transfer), tuning.py
│   ├── stats/                          # bootstrap.py, paired.py, holm.py, null_control.py, noise_floor.py, power.py
│   ├── evaluation/                     # e0.py, e1.py, e2.py → tables and figures; frontier.py
│   ├── orchestration/                  # jobs.py (Postgres queue), stages.py, state.py, artifacts.py, worker.py
│   ├── api/                            # FastAPI app: routers, schemas, errors (RFC 9457), deps
│   └── testing/                        # mock_upstream.py (OpenAI-compatible SSE replay), fixtures, fake clock
├── apps/console/                       # Next.js + TypeScript (Day 9)
├── services/                           # one directory per deployable image: Dockerfile + entrypoint config only
│   ├── shaper/  loadgen/  capture/  netinit/  worker/  api/  console/
├── deploy/
│   ├── compose/lab.yml                 # vllm, shaper, shaper-netinit, loadgen, capture (+ mock-upstream profile)
│   ├── compose/platform.yml            # postgres, api, worker(s), console, prometheus/grafana (profile obs)
│   └── gce/                            # cloud-init + compose overrides for GPU and client VMs
├── infra/terraform/{modules/{network,gpu_server,client,storage,registry,iam,budget,nlb},envs/dev}
├── experiments/                        # versioned YAML: validation.yaml, e0.yaml, e1.yaml, e2.yaml, cloud-mini.yaml
├── workloads/corpus/v1/                # class/family/template/slot YAML → built corpus artifact (hash-pinned)
├── bench/                              # runtime/, shaper/, capture/ harnesses + RESULTS.md
├── observability/                      # prometheus.yml, grafana dashboards (2)
├── tests/                              # unit/, property/, integration/, lab/, shaper_contract/, compat/ (python + node), ml_leakage/
├── docs/                               # PRODUCT.md, RESEARCH.md, IMPLEMENTATION_PLAN.md, adr/, runbooks/, experiments/
└── .github/workflows/                  # ci.yml, images.yml, infra.yml
```

**Import-linter contracts (CI-enforced):**
- `crosstalk.shaper` may import only `common` and `contracts`, which keeps the shaper image lean.
- `crosstalk.features`, `crosstalk.attacks` and `crosstalk.stats` must not import `crosstalk.traces.oracle`, `crosstalk.loadgen`, `crosstalk.shaper` or `crosstalk.workloads.text`.
- `crosstalk.capture` must not import ML modules.
- Nothing imports `crosstalk.testing` outside `tests/`.

---

## 6. Local development model

### 6.1 Host

- Linux, or Windows 11 + **WSL2 Ubuntu 24.04** with Docker Engine and the NVIDIA Container Toolkit (or the Docker Desktop WSL2 backend).
- Reference workstation: RTX 3050 Laptop 6 GB, 16 GB RAM, i5-12450HX.
- **Day-1 smoke test:** the vLLM container serves one streamed completion on the GPU. If it fails, ADR-013 falls back to running E0–E2 pilots on the cloud L4 from Day 3, and local work uses the mock upstream.
- WSL `/tmp` is cleared when the distro stops. Artifacts live under the repo's `artifacts/` directory, which is gitignored and inside the WSL filesystem for I/O speed.

### 6.2 Lab topology (`deploy/compose/lab.yml`)

```text
             network "edge" (bridge, MTU 1500)                    network "backend" (bridge, internal)
  ┌──────────────────────────────────────────────┐        ┌─────────────────────────────────────────┐
  │ loadgen ──(TLS 1.3, HTTP/1.1, :8443)──► shaper ├────────┤ shaper ──(HTTP/1.1, :8000)──► vllm (GPU) │
  │   ▲ netns shared with                         │        └─────────────────────────────────────────┘
  │ capture (dumpcap; NET_RAW, NET_ADMIN)         │   shaper-netinit (NET_ADMIN, network_mode: service:shaper) → offloads off, then exits
  └──────────────────────────────────────────────┘
```

- **vllm:** `vllm/vllm-openai@sha256:…` (v0.29.0). Local model `Qwen/Qwen2.5-1.5B-Instruct` at a pinned revision (Apache-2.0, ungated; 0.5B if memory-bound). Args: `--stream-interval 1 --max-num-seqs 64 --seed 0 --gpu-memory-utilization 0.85 --max-model-len 4096`; prefix caching left at the engine default and **isolated per request** via a unique `cache_salt` (§13; E4 excepted). Healthcheck `GET /health`. Attached only to `backend`.
- **shaper:** `crosstalk-shaper` image; `:8443` on `edge`, admin `:9100` on `backend`; mounts the lab TLS key/cert (read-only) and `artifacts/shaper-log/` (write). The shaper does not know session IDs. It appends emission rows keyed by `request_id` to rolling NDJSON segments (`shaper-log/<pid>-<seq>.ndjson`). The `collect` stage's finalizer extracts the session's `request_id`s into `sessions/<id>/oracle/emissions.parquet`. Container is `read_only: true`, `cap_drop: [ALL]`, non-root.
- **shaper-netinit:** `crosstalk-netinit` (debian-slim + ethtool + iproute2), `network_mode: service:shaper`, `cap_add: [NET_ADMIN]`. Runs `ethtool -K <if> tso off gso off gro off lro off` on every non-loopback interface, writes `ethtool -k` output to `artifacts/lab/offloads-shaper.json`, and exits 0.
- **loadgen:** `crosstalk-loadgen`; runs the session executor and the `collect` worker queue (Day 7); mounts `artifacts/` (write) including `restricted/`.
- **capture:** `crosstalk-capture` (debian-slim + tshark/dumpcap), `network_mode: service:loadgen`, `cap_add: [NET_RAW, NET_ADMIN]`. At start it disables `gro lro tso gso` on the loadgen interface, records `ethtool -k`, and exposes a local control API on `127.0.0.1:9200` (shared netns) that starts and stops dumpcap per session.
- **mock-upstream** (profile `mock`): `crosstalk.testing.mock_upstream` replays token timelines. It replaces vLLM in CI and in shaper benchmarks.

### 6.3 Developer commands (Makefile → CLI)

```bash
make bootstrap        # uv sync --locked; pnpm i; pre-commit install
make lab-certs        # local CA + shaper cert into .secrets/ (gitignored)
make lab-up           # docker compose -f deploy/compose/lab.yml up -d --wait
make lab-check        # verifies offloads, vLLM health, shaper readyz, capture control
make session PLAN=experiments/validation.yaml   # plan + run a session (capture on)
make reconstruct S=<session_id>                  # pcapng → packets/records/flows (+ tshark validation)
make e0 | e1 | e2     # full experiment pipeline via CLI (Day 3–6), via API (Day 7+)
make test | lint | typecheck | bench-runtime
```

---

## 7. Networking architecture

| Item | Specification |
|---|---|
| Client-facing protocol | HTTP/1.1 over TLS 1.3; `POST /v1/chat/completions` with `stream: true`, `stream_options: {"include_usage": true}` |
| TLS server config | `ssl.PROTOCOL_TLS_SERVER`; `minimum_version = maximum_version = TLSv1_3`; ALPN `["http/1.1"]`; `num_tickets = 0` (baseline; recorded); no client auth; ECDSA P-256 cert from the lab CA. The negotiated suite is recorded per flow (visible in ServerHello). All TLS 1.3 AEAD suites add 17 bytes per record |
| Connection policy | One request per connection in the baseline: the client sends `Connection: close` and opens a new TCP+TLS connection per request. Keep-alive is a P1 arm |
| Socket options | `TCP_NODELAY` on both ends (asyncio default; asserted at startup) |
| Experiment headers (lab mode only) | `x-ct-arm: aN` (exactly 2 chars) and `x-ct-rid: <ULID>` (exactly 26 chars). **Fixed width, so request size is identical across arms (test-enforced).** Production mode rejects them with 400 |
| MTU | Lab bridges 1500; GCP VPC 1460 (default). MTU recorded per session |
| Offloads | Disabled on the capture interface (GRO/LRO/TSO/GSO) and on the shaper's client-facing interface (TSO/GSO/GRO) in the lab; state recorded in the manifest. Cloud: disabled if the NIC driver allows; otherwise recorded, and packet-layer features are flagged (§9) |
| Time sources | Capture: kernel timestamps in pcapng (resolution from IDB `if_tsresol`; ≥ µs required). Loadgen/shaper: `time.monotonic_ns()` plus a `(realtime_ns, monotonic_ns)` pair logged at session start. Cross-layer alignment uses sequence matching, not cross-host clocks |

---

## 8. Capture architecture

1. **Session start:** the loadgen calls `POST 127.0.0.1:9200/capture/start {session_id, bpf: "tcp port 8443", ring_mb}`.
2. The capture supervisor (Python) launches `dumpcap -i <if> -f "tcp port 8443" -s 0 -B 64 -n -w artifacts/sessions/<id>/pcap/cap.pcapng -b filesize:524288 -q`. Full snaplen is mandatory: TLS headers can start anywhere in a segment.
3. **Session end:** `POST /capture/stop` sends SIGINT to dumpcap and waits for it to exit. The supervisor parses dumpcap's final statistics (packets captured, dropped by kernel and by interface), reads pcapng Interface Statistics Blocks (`isb_ifdrop`, `isb_osdrop`) where present, and writes `capture.json` (drops, versions, command line, offload snapshot, MTU, interface).
4. **Completeness gate:** any non-zero kernel/interface/buffer drop makes the session `INVALID` for reporting, as does any flow with a TCP hole or a record-parse desync (§10).
5. **Retention:** raw pcapng is kept for every session in the lab (validation and reproducibility). In the cloud it is uploaded to the artifacts bucket with a lifecycle rule (30 days, then Nearline). PCAPs contain only ciphertext. **TLS key logs exist only in validation sessions, are stored under `restricted/keylogs/`, and are never readable by analysis jobs.**

Fallback: `tcpdump -w … --time-stamp-precision=nano -s 0 -B 65536`. The reader supports classic pcap (µs/ns magic) as well as pcapng.

---

## 9. Offload handling

| Location | Action | Verification |
|---|---|---|
| Capture interface (client side) | `ethtool -K <if> gro off lro off tso off gso off` | `ethtool -k` snapshot in `capture.json`. Reconstruction asserts `max(ip_len) ≤ MTU` over the session; a violation marks packet-layer features invalid |
| Shaper client-facing interface | `ethtool -K <if> tso off gso off gro off` (netinit container) | Snapshot in `offloads-shaper.json`. The same `max(ip_len)` check on s→c packets |
| Cloud client VM NIC | Same commands on the VM NIC (gVNIC/virtio). If a feature is `[fixed]`, record it | Snapshot per VM. If offloads cannot be disabled, packet-layer features are reported with `offload_state="on"` and are excluded from primary results. The TLS record layer is unaffected |
| Checksum offload | Leave enabled; ignore checksum validity in the parser (captured pre-computation checksums can be wrong) | Parser never rejects on checksum |
| Loopback | **Never** capture the measured leg on `lo` (MTU 65,536, TSO/GSO/GRO on) | Lab topology uses veth/bridge; lab-check fails if the endpoint resolves to loopback |

---

## 10. TLS-record reconstruction and validation

### 10.1 Algorithm (`crosstalk.capture.reconstruct`)

1. **Read** pcapng/pcap (block/record parser, `memoryview`, no per-packet subprocess). Link types: Ethernet (1) and Linux SLL/SLL2. Reject truncated packets (`caplen < origlen`); none are expected with snaplen 0.
2. **Parse** IPv4/IPv6 and TCP. Flow key = sorted 4-tuple; direction c→s when the destination port is 8443.
3. **Reassemble** each direction:
   - The initial sequence number comes from the SYN (flows without one are marked `incomplete`).
   - Keep `next_seq`. In-order bytes are appended with their arrival timestamp.
   - Out-of-order segments go into a bounded interval map. Duplicates and retransmissions are dropped and their bytes counted (`retrans_bytes`).
   - Overlaps must be byte-identical, otherwise the flow is flagged `overlap_conflict`.
   - A hole remaining at FIN/RST/session end marks the flow `incomplete`.
   - Each stream byte gets `t_first_seen`. Record timestamps derive from it.
4. **Parse records** sequentially over each reassembled stream. Header validation: type ∈ {20,21,22,23}, version ∈ {0x0301, 0x0303}, length ≤ 16,384 + 256. A violation is a desync, and the flow is excluded.
   - `t_start` = first-seen time of the record's first byte.
   - `t_end` = max first-seen time over its bytes, i.e. the time the record became complete. This is what the observer can use.
5. **Phase labelling** (public TLS 1.3 message flow for the P0 profile; attacker-computable):
   - c→s: `22 ClientHello` → optional `20 CCS` → first `23` = client Finished (record length 53 with SHA-256 suites, 69 with `TLS_AES_256_GCM_SHA384`) → subsequent `23` records = **request**.
   - s→c: `22 ServerHello` → optional `20` → `23` handshake flight (records before the client Finished) → `23` records starting after the first request record = **response**.
   - A final 19-byte `23` record in either direction is labelled `probable_alert`. It is kept in the tables; features treat it like any other record because the observer cannot decrypt it.
6. **Outputs:** `packets`, `records`, `flows` Parquet (§14). The `flow_map` joins flows to `request_id` using `observer/flow_hints.parquet` (`request_id`, `client_port`, `t_connect_ns`), which the collect stage exports. It uses only ports and times, never payload.

### 10.2 Validation (tests + per-session gate)

| Check | Method | Threshold |
|---|---|---|
| Parser ≡ tshark | Run tshark in bulk once per validation session: `tshark -r cap.pcapng -o tcp.desegment_tcp_streams:TRUE -o tls.desegment_ssl_records:TRUE -Y "tcp.port==8443 && tls" -T fields -E separator=/t -e frame.number -e frame.time_epoch -e tcp.stream -e tcp.srcport -e tls.record.content_type -e tls.record.length`. Compare per flow and direction: sequence of (content_type, length) and the end-frame timestamp | **100%** sequence equality. Timestamps equal to the frame of record completion (±1 µs) |
| Emission → record | Align shaper emissions (oracle) with s→c response records per request, by order and predicted length `framed_len + 17` | ≥ 99.99% exact one-to-one in the baseline. Every exception is listed with a cause (e.g. `> 16 KiB split`) |
| Keylog decryption (validation sessions) | `tshark -o tls.keylog_file:… -T fields -e tls.record.length -e http.file_data` to confirm record plaintext equals emission bytes for a sample | 100% on sample |
| Client record oracle | Loadgen validation mode logs record headers from the raw ciphertext (MemoryBIO client) | Equal to the reconstructed s→c records |
| Robustness | Replay the validation session with `tc netem loss 1% reorder 5%`; compare reconstructed records with the emission log | 100% records recovered for complete flows. Incomplete flows flagged, never silently used |
| Throughput | Reconstruct a 1 GiB pcapng | ≥ 200k packets/s on the workstation (a multiprocessing split by flow is allowed) |

---

## 11. Shaper architecture

### 11.1 Process model

One Python process runs one uvloop event loop. `SO_REUSEPORT` multi-process mode (`--workers N`) is available and required above the validated load (§3.2). No threads in the hot path. File I/O for the oracle log runs through a single background flusher (`asyncio.to_thread` every 250 ms, bounded buffer).

### 11.2 Request lifecycle

1. **Accept** TLS (`loop.create_server(..., ssl=ctx, backlog=4096)`). `ClientConnection` is an `asyncio.Protocol` holding an `h11.Connection`, a `write_ok` event driven by `pause_writing`/`resume_writing`, and a strong reference to its stream task (tracked in a `set`, discarded on completion; Day-0 lesson: never rely on weak task refs).
2. **Parse** the request: method/path allow-list (`POST /v1/chat/completions`), body ≤ 256 KiB, JSON validated by a Pydantic model (lenient: unknown fields passed through), lab headers resolved to a `PolicyConfig` from the policy set loaded at startup.
3. **Upstream:** aiohttp `POST http://vllm:8000/v1/chat/completions`, timeouts `connect=5 s`, `first_byte=120 s`, `read_idle=30 s`. The response is read with `iter_any()` into the SSE parser. Upstream backpressure: stop reading when the stream's pending queue exceeds its cap (1 MiB or 4,096 events).
4. **Emitter loop** (per stream): feeds upstream events to the policy (`on_event`), schedules `loop.call_at` for `policy.next_deadline_ns()`, and executes `Emit` actions via the framer. Each emission is exactly **one** `transport.write(bytes)` of ≤ 16,384 bytes. Larger payloads are split by the framer into explicitly logged sub-emissions.
5. **Backpressure:** if `transport.get_write_buffer_size()` exceeds the high-water mark (64 KiB), the emitter stops emitting and the policy is informed (`on_stall`). The stall is logged in accounting. If it persists beyond `stall_timeout` (30 s), the stream aborts with a final SSE error comment and the connection closes.
6. **Completion:** the upstream `[DONE]` or EOF triggers `policy.on_upstream_end`, which drains per policy (D3 tail). Then the framer writes the terminator (`data: [DONE]` event + `0\r\n\r\n`) as **one** emission, and the connection closes (TLS `close_notify`).
7. **Cancellation:**
   - Client disconnect cancels the upstream request and marks the stream `client_aborted`.
   - Upstream error or timeout sends an OpenAI-style `data: {"error": {...}}` event, closes the connection, and marks the stream `upstream_error`.
   - Shaper shutdown (SIGTERM): stop accepting, drain up to 60 s, abort the rest with a logged reason.

### 11.3 SSE parser (`sse.py`)

WHATWG event-stream semantics: CR, LF and CRLF line endings; comment lines ignored; `data` lines joined with `\n`; dispatch on a blank line; BOM stripped at stream start. Each event keeps its **raw bytes** (exact passthrough) and its parsed fields. Property-tested against arbitrary chunking.

### 11.4 Framer (`framer.py`): exact sizes

- **Chunk framing:** `hex(L) CRLF payload CRLF`. Record plaintext = framed length. Wire record length = framed + 17 (+5-byte header).
- **Padding** is carried in an SSE comment line placed **before** the `data:` line of the same event (`:<filler>\n`). Filler is a fixed printable byte (`.`), so it compresses nothing and leaks nothing.
- **Exact-size algorithm:** given the SSE payload `p` and target framed size `T`:
  1. If `framed(p) == T`, emit `p` unchanged.
  2. Otherwise, for hex width `w ∈ {1..4}` let `L = T − 4 − w`. Accept if `len(hex(L)) == w` and `L ≥ len(p) + 2`; then `filler = L − len(p) − 2` (the comment line costs `filler + 2` bytes).
  3. If no width fits, use the next bucket.

  Unit-tested exhaustively for T ∈ [16, 16384].
- **Merge:** concatenates `choices[0].delta.content` of consecutive mergeable events. Mergeable means content-only deltas with no tool_calls/logprobs/finish_reason/usage. It keeps `id/object/created/model` from the first event and emits compact JSON. Non-mergeable events flush the merge buffer and are emitted as their own units. Fidelity property: the concatenated content is identical.
- **Dummy block:** `:<filler>\n\n` sized to the target. Comment-only blocks produce no event in the verified clients (RESEARCH §5.2).

### 11.5 Admin and metrics (`admin.py`, `aiohttp.web`, admin port on the backend network)

- `/healthz` (process alive).
- `/readyz` (policy set loaded, upstream `/health` OK, TLS context loaded).
- `/policies` (loaded policy set with a sha256 per policy).
- `/metrics` (Prometheus):
  - `ct_shaper_streams_active`, `ct_shaper_emissions_total{arm,kind}`, `ct_shaper_pad_bytes_total{arm}`
  - `ct_shaper_queue_delay_seconds{arm}` (histogram: content arrival → emission), `ct_shaper_timer_lateness_seconds{arm}` (histogram)
  - `ct_shaper_loop_lag_seconds` (100 ms heartbeat), `ct_shaper_stalls_total`, `ct_shaper_upstream_errors_total{code}`, `ct_shaper_stream_outcome_total{outcome}`
- Labels are bounded: `arm` ∈ 6 values; no request IDs.

### 11.6 Configuration

`ShaperSettings` (pydantic-settings, env prefix `CT_SHAPER_`): listen address, cert/key paths, upstream URL, mode (`lab|prod`), policy-set file (YAML → `list[PolicyConfig]`, validated at startup, immutable for the process lifetime), limits (body size, queue caps, high-water mark, timeouts), oracle log directory, workers. There is **no hot reload**: a changed policy set means a new process, and its hash goes into the session manifest.

---

## 12. Defense interface and policies

### 12.1 Interface (`policies/base.py`)

```python
@dataclass(frozen=True, slots=True)
class EventMeta:                 # what a policy may see: sizes and flags, never text
    raw_len: int                 # bytes of the upstream SSE event
    content_len: int             # UTF-8 bytes of delta.content (0 if none)
    mergeable: bool
    terminal: bool               # finish_reason / usage / [DONE]
    arrival_ns: int

@dataclass(frozen=True, slots=True)
class Emit:
    take: int                    # number of pending events (from the head) to include
    merge: bool                  # merge contents into one chunk (only if all taken are mergeable)
    pad_to: int | None           # target framed size (bytes) or None
    dummy: bool = False          # comment-only block of size pad_to (take must be 0)

class Policy(Protocol):
    def on_start(self, now_ns: int) -> list[Emit]: ...
    def on_event(self, meta: EventMeta, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]: ...
    def on_timer(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]: ...
    def on_upstream_end(self, pending: Sequence[EventMeta], now_ns: int) -> list[Emit]: ...
    def next_deadline_ns(self) -> int | None: ...
```

**Invariants (tests):**
1. **Content fidelity:** decoded content equals upstream content (property tests, every policy).
2. **No content-dependent timing:** policies receive `EventMeta` only (type-enforced), so decisions depend on sizes, counts and clock, never text.
3. **Termination:** every stream ends within `upstream_end + tail_bound`.
4. **Bounded memory.**
5. **Accounting completeness:** the sum of emission bytes equals the reconstructed s→c response-record plaintext bytes (validated per session).

### 12.2 Arms

| Arm id | Policy | Parameters (defaults frozen before E1 from pilots) | Behaviour |
|---|---|---|---|
| a0 | **D0 passthrough** | — | `Emit(take=1, merge=False, pad_to=None)` per upstream event, immediately |
| a1 | **D1 bucket padding** | `ladder_bytes`: first bucket = p99.9 of single-event framed size in the E0 pilot, rounded up to 16 B; then ×2 up to 16,384 | Each event padded to the smallest bucket ≥ its framed size |
| a2 | **D2 window batching** | `window_ms = 100` (grid anchored at the first content event), `max_merge_bytes = 8192` | At each grid tick with pending content: one merged emission. The role/first event goes immediately (TTFT unchanged). Terminal events (finish/usage/`[DONE]`) flush any pending merge at the next tick and are emitted after it, in order |
| a3 | **D3 constant-rate** | `interval_ms = 50`, `record_size` = D1's first bucket × 2 (≥ 4 tokens capacity), `tail_quantum_ticks = 32`, `start = on_request` | A tick every T from request arrival. Each tick emits exactly one record of `record_size`: merged pending content up to capacity (padded), or a dummy comment block. Continues until upstream ends, the queue is empty, and the tick count ≡ 0 mod K. Backlog is carried over, so latency accumulates when generation outpaces capacity |
| a4 | **D4 hybrid** | `window_ms = 100` + D1 ladder | D2 merged emissions padded to the D1 ladder |
| a5 | D1r random padding (P1) | `max_pad_bytes = 64`, uniform | Industry-style reference |

---

## 13. Workload engine

- **Corpus v1** (`workloads/corpus/v1/`):
  - 6 classes (medical, legal, finance, programming, travel, cooking).
  - ≥ 40 prompt families per class; 5 paraphrase templates per family (1,200 prompts).
  - A 2-prompt-per-class determinism set.
  - Authored as YAML (class → subtopics → families → paraphrases with slots), reviewed, committed. `crosstalk workloads build` renders prompts and produces `corpus.parquet` + `manifest.json` (content hash).
  - Token counts use the served model's tokenizer (`tokenizers`, revision pinned). Byte lengths recorded.
  - Banned-pattern check (emails, phones, IDs).
- **Regimes:**
  - **NAT:** prompt as authored; `max_completion_tokens = 512`; `temperature = 0.7`; per-request `seed` from the plan.
  - **MATCH-P:** stratified subsample with identical per-class histograms over 8-token prompt buckets; trailing-space padding to 32-byte buckets. A test asserts identical request-record size distributions per class.
  - **MATCH-PR:** MATCH-P plus `min_tokens = max_completion_tokens = L`, with L ∈ {96, 160, 224} assigned by a class-independent permutation. Validated by `completion_tokens == L` from `usage`.
- **Prefix-cache control:** every request carries vLLM `cache_salt = request_id` (a fixed-width 26-char ULID). Only requests with the same salt can share cached blocks ([R18]), so cross-request prefix reuse is disabled without altering prompt text or request size. E4 is the only experiment that shares salts deliberately.
- **Session plan** (`crosstalk session plan <experiment.yaml>` → `plan.parquet`):
  - Rows are slots: `(slot, block, block_concurrency, request_id, prompt_id, arm, stream_interval, regime, sampling params, class, family_id, template_id, target_len)`.
  - Block-randomized: every randomization block contains each (class × arm) combination once, in shuffled order, with a seeded RNG.
  - E2 plans add time-alternating concurrency blocks (§16).
  - The determinism set is interleaved at a fixed rate (1 in 25 slots).
- **Executor** (`crosstalk session run`): closed-loop concurrency C (semaphore; C changes only at block boundaries after the previous block fully drains). Warm-up slots are excluded from analysis (first 30 of each block). No think time. It records `requests`, `client_events` and restricted text.

---

## 14. Data schemas and access rules

### 14.1 Directory layout (local; mirrored to GCS in the cloud)

```text
artifacts/
├── sessions/<session_id>/
│   ├── manifest.json                 # RunManifest (§14.4)
│   ├── plan.parquet                  # oracle (labels + arms)
│   ├── pcap/cap_*.pcapng             # observer raw (ciphertext only)
│   ├── capture.json                  # drops, offloads, versions
│   ├── observer/{packets,records,flows,flow_hints,flow_map}.parquet
│   ├── oracle/{emissions,requests,client_events}.parquet
│   └── validation/{tshark_agreement,emission_fidelity,keylog_check}.json
├── restricted/<session_id>/{text.parquet, keylogs/}   # plaintext + key logs: loadgen/QA only
├── labels/<session_id>/labels.parquet                 # class/family/template/regime; no text
├── shaper-log/<pid>-<seq>.ndjson                      # shaper emission segments (oracle; extracted per session by `collect`)
├── lab/offloads-*.json                                # offload snapshots from netinit/capture
└── experiments/<experiment_id>/{features/,models/,predictions/,evaluations/,figures/,report.md}
```

### 14.2 Tables (pyarrow schemas in `contracts/tables.py`; all rows carry `session_id`)

| Table | Key columns (type) | Visibility |
|---|---|---|
| `packets` | flow_id (i32), dir (i8: +1 s→c, −1 c→s), ts_ns (i64), ip_len (u16), tcp_len (u16), flags (u8), rel_seq (u32), is_retrans (bool), is_ooo (bool) | Observer |
| `records` | flow_id, dir, idx (i32), t_start_ns, t_end_ns (i64), content_type (u8), length (u16), stream_offset (i64), phase (enum: handshake/request/response/post) | Observer |
| `flows` | flow_id, client_port (u16), server_port, t_syn_ns, t_last_ns, bytes/packets/records per dir, retrans_bytes per dir, max_ip_len, cipher_suite (u16), complete (bool), anomalies (list<str>) | Observer |
| `flow_hints` | request_id (str[26]), client_port (u16), t_connect_ns (i64) | Observer-join input (written by collect) |
| `flow_map` | flow_id → request_id (str[26]) | Observer-join (ports/times only) |
| `emissions` | request_id, seq (i32), t_emit_ns, kind (content/merged/padded/dummy/terminator), framed_len, content_bytes, pad_bytes, n_events, queue_delay_ns, timer_lateness_ns, arm | **Oracle** |
| `requests` | request_id, slot, arm, stream_interval, client_port, t_connect_ns, t_tls_ns, t_sent_ns, t_first_byte_ns, t_first_content_ns, t_done_ns, status, error_code, prompt_tokens, completion_tokens | **Oracle** (performance ground truth) |
| `client_events` | request_id, idx, t_recv_ns, is_content, framed_len | **Oracle** |
| `labels` | request_id, prompt_id, class, family_id, template_id, regime, prompt_bucket, target_len, t2_sensitive (bool) | Labels (allowed for training/eval) |
| `text` | request_id, prompt_text, response_text | **Restricted** |
| `features_r1/r2` | request_id, arm, feature columns (f32) | Derived observer |
| `predictions` | request_id, fold, arm, setting (static/adaptive/transfer), model, seed, y_true, proba (list<f32>) | Results |
| `evaluations` | experiment_id, arm, setting, model, metric, value, ci_lo, ci_hi, n, n_families, B | Results |

### 14.3 Enforcement of the threat model

1. **Loader guards:** `crosstalk.traces.observer` opens only `observer/` and `labels/`. It raises `OracleAccessError` on any other path, and `crosstalk.features`/`attacks` can only reach data through it.
2. **import-linter contracts** (§5).
3. **Containers (two worker classes):**
   - `attack` workers run feature extraction and attacker training/prediction. They mount only `sessions/*/observer`, `labels/` and `experiments/` (bind mounts per subdirectory): no `oracle/`, no `restricted/`.
   - `evaluate` workers run validation, cost metrics, statistics and reports. They may read `oracle/` (read-only) and predictions, but never `restricted/`.
4. **Cloud:** a separate `restricted` bucket that the analysis service account cannot read; a test asserts HTTP 403.
5. **Tests:**
   - Feature matrices contain no oracle-derived columns (schema allow-list).
   - A canary: a prompt containing a unique token string never appears in any `observer/`, `features/` or model artifact (byte search).

### 14.4 Run manifest (`contracts/manifest.py`)

`manifest.json` holds:
- **Identity:** `session_id`, `experiment_id`, `config_sha256`, `plan_sha256`, `git_commit`, `git_dirty` (must be false for reportable runs).
- **Artifacts:** image digests (shaper, loadgen, capture, vllm), `vllm_version`, `model_id`, `model_revision`, `tokenizer_revision`, `workload_version`, `workload_sha256`.
- **Policies and seeds:** policy-set sha256 plus each policy's parameters; seeds (plan, sampling, model); `stream_interval` settings; concurrency.
- **Environment:** host kernel, CPU model, GPU model and driver, OpenSSL version, Python version, uvloop version, MTU, offload snapshots, capture command and dumpcap/tshark versions.
- **Timing:** start/end realtime and monotonic pairs.
- **Outcome:** counts (planned, completed, failed), capture drops, validation results, and `status ∈ {VALID, INVALID, PARTIAL}` with reasons.

---

## 15. ML pipeline

### 15.1 Features (TLSRecord layer, response phase unless stated; `features/`)

- **R1 summary (~90 features):**
  - Request side: c→s request bytes and record count.
  - Response record count, total bytes, size mean/std/min/max and quantiles (10/25/50/75/90/99), histogram over 16 fixed bins.
  - Inter-arrival quantiles (10/50/90/99), mean, coefficient of variation.
  - Observable TTFT (first response record `t_start` − last request record `t_end`), total duration, records/s.
  - Bursts (gap < 20 ms): count, mean and max size.
  - First 20 record sizes and gaps, last 5 sizes.
- **R2 CUMUL:** signed cumulative bytes over all post-handshake records, linearly interpolated at 100 points, plus 4 totals.
- **R1p/R2p:** the same over `packets` (secondary; only for sessions with `offload_state=off`).
- **T-len diagnostic subset:** counts, totals and durations only.
- Feature code is pure (Polars expressions), golden-vector tested, and versioned (`feature_schema_version`).

### 15.2 Models

- **A0:** `DummyClassifier(strategy="stratified")` and majority.
- **A1:** `StandardScaler` → `LogisticRegression(class_weight="balanced", max_iter=5000)`, with `C` chosen in the inner CV ∈ {0.01, 0.1, 1, 10}.
- **A2:** LightGBM (`objective=multiclass|binary`, `learning_rate=0.05`, `num_leaves ∈ {15, 63}`, `min_child_samples=20`, `feature_fraction=0.8`, `bagging_fraction=0.8`, `n_estimators ≤ 2000` with early stopping 50 on the inner validation fold).
- The same grid and budget apply to every arm and setting. Model seeds: 5.

### 15.3 Splits and protocol (`attacks/splits.py`, `attacks/protocols.py`)

- **Outer:** `StratifiedGroupKFold(n_splits=5, groups=family_id, shuffle=True, random_state=plan_seed)`. **Fold assignment is computed once per experiment over families and reused for every arm and setting**, which makes the comparisons paired.
- **Inner:** `GroupKFold(3)` on training families for hyperparameters and early stopping. Scaling, feature selection, thresholds and calibration are fitted inside the inner loop only (sklearn `Pipeline`).
- **Static:** fit on arm a0 traces of training families → predict arm-k traces of test families (and a0 test traces).
- **Adaptive:** fit on arm-k traces of **training families only** → predict arm-k traces of the same test families.
- **Robustness checks** (reported, not primary):
  - Template-disjoint: hold out one template index per family.
  - Session-disjoint: train on session 1, test on session 2, with families still disjoint.
- **Learning curve:** training on 25/50/75/100% of training families for D0 and the flagship arms.
- **Outputs:** out-of-fold predictions for every trace, setting, model and seed (`predictions`).

### 15.4 Null control and noise floor

- **Null control:** permute the family → class mapping (preserving class counts) and run the full D0 static pipeline. 20 permutations per regime. Each null balanced accuracy must lie within its bootstrap CI of chance. The observed result is reported with the permutation p-value.
- **Noise floor:** the determinism set (2 prompts × 6 classes) repeated throughout each session. Report within-prompt vs between-prompt variance of record count, duration and TTFT (intraclass correlation). Report the drift of D0 per-prompt features across the session (slope with a CI).

---

## 16. Statistics (`stats/`)

- **Unit of resampling:** prompt family (clusters of paraphrases and repeats), stratified by class.
- **Paired cluster bootstrap** (B = 2,000 during development, 10,000 for final results):
  1. Resample families with replacement within each class.
  2. Take all traces of the sampled families (with multiplicity).
  3. Compute every metric for every (arm, setting) on the **same** resample.
  4. Derive Δ = m(arm, setting) − m(a0, static) and the retained-leakage ratios ρ.
- **Reported quantities:** percentile CIs (BCa optional), and bootstrap two-sided p-values for pre-declared tests.
- **Retained leakage:** ρ = (m − chance)/(m_D0 − chance). Chance is 1/K for balanced accuracy and the prevalence for PR-AUC. Undefined if the D0 CI includes chance (reported as n/a).
- **E2 design against drift:**
  - Each E2 session runs **time-alternating concurrency blocks** (C1 → C8 → C32, repeated), drained between blocks, with all arms interleaved inside every block.
  - Two replicate sessions are collected on different days.
  - Concurrency contrasts are computed within each session and combined with a **two-stage bootstrap** (sessions, then families), so load is not confounded with drift.
  - The determinism probe tracks residual drift per block.
- **Primary comparisons and multiplicity (declared in `experiments/*.yaml` before collection):**
  - E1: for k ∈ {1..4} and setting ∈ {static, adaptive}, Δ balanced accuracy vs a0 static (8 tests).
  - E2 (5 tests):
    - D0 leakage difference m(a0, C=32) − m(a0, C=1).
    - For k ∈ {1..4}, ρ_adaptive(k, C=32) − ρ_adaptive(k, C=1), where ρ is normalised by D0 at the same concurrency.
  - **Holm–Bonferroni** at α = 0.05 within each family. Everything else is exploratory with unadjusted CIs.
- **Never** use non-overlap of separate CIs as a test.
- **Power check (Day 3):** from the E0 pilot, estimate the bootstrap SE of a paired Δ. If the projected 95% half-width at the planned n exceeds 5 pp, raise n per class (up to 300) or document the resolution limit.
- **MI (exploratory):** a lower bound I(Y;Ŷ) from pooled confusion matrices, with a bootstrap CI (P1). kNN estimators are P2.

---

## 17. Orchestration

- **CLI first (Days 2–6):** every stage is a CLI command operating on artifact directories. The same functions become worker jobs on Day 7.
- **Stages:**
  - `plan` → `collect` (loadgen host; drives capture) → `reconstruct` → `validate` → `features`
  - Then, at experiment level: `attack(arm, setting)` → `stats` → `report`.
- **Idempotency:**
  - Stage key = sha256(stage name, input artifact hashes, config hash, code commit). A stage whose key has a completed record is skipped.
  - Outputs go to `…/.tmp-<uuid>/` and are then atomically renamed. Each output file's sha256 is registered.
- **Day 7 durable state (PostgreSQL, Alembic migrations):**
  - Tables `experiments`, `sessions`, `jobs`, `artifacts`, `events`.
  - `jobs(id, kind, queue ∈ {collect, attack, evaluate}, payload jsonb, state ∈ {queued, running, succeeded, failed, dead, cancelled}, attempts, max_attempts=5, run_after, lease_until, worker_id, stage_key UNIQUE, error jsonb, created_at, updated_at)`.
  - Claiming: `UPDATE … SET state='running', lease_until=now()+interval '60s' … WHERE id = (SELECT id FROM jobs WHERE state='queued' AND queue=$1 AND run_after<=now() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`.
  - A heartbeat extends the lease every 20 s. Expired leases are re-queued by a reaper.
  - Retries use exponential backoff with full jitter (base 5 s, cap 5 min). After `max_attempts` the job is `dead` and the parent becomes `failed`.
- **Session states:** `PLANNED → COLLECTING → COLLECTED → RECONSTRUCTED → VALID | INVALID | PARTIAL`, plus `CANCELLED`. Experiment state is derived: `running | completed | partial | failed | cancelled`.
- **Cancellation:** `cancel` sets `cancelled` on queued jobs and signals running jobs through a DB flag checked between slots (collect) or between folds (attack).
- **Workers and stage-to-queue mapping:**
  - `collect` (plan, collect, emission-log extraction) is consumed only by the loadgen container, which owns capture control.
  - `attack` (reconstruct, features, attack) is consumed by observer-only `crosstalk-worker` containers.
  - `evaluate` (validate, cost metrics, stats, report) is consumed by oracle-allowed `crosstalk-worker` containers (§14.3).

  Reconstruction reads only PCAP and the port/time columns needed for `flow_map`, which the collect stage exports as `observer/flow_hints.parquet`.

---

## 18. API (FastAPI; Day 7; `/v1`)

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | `/experiments` | Create from config (validated `ExperimentConfig`) | Returns 201 + id. Config hash returned |
| POST | `/experiments/{id}/runs` | Plan and enqueue all stages | Requires the `Idempotency-Key` header (stored 24 h) |
| GET | `/experiments` / `/experiments/{id}` | List (cursor pagination) / detail with derived state | — |
| GET | `/sessions/{id}` | Session manifest, stage states, validation results | — |
| POST | `/experiments/{id}/cancel` · `/jobs/{id}/retry` | Control | Idempotent |
| GET | `/sessions/{id}/traces/{request_id}?layer=records\|packets` | Observer layers for the Trace Explorer | Downsampled above 20k points |
| GET | `/sessions/{id}/traces/{request_id}/emissions` | Oracle layer (validation view) | Requires `scope=oracle` (operator token). Marked "oracle" in the UI |
| GET | `/experiments/{id}/results` | Evaluations with CIs, static/adaptive pairs | — |
| GET | `/experiments/{id}/frontier?setting=static\|adaptive&cost=ttft_p95\|itl_p99\|bandwidth\|cpu` | Frontier points | — |
| GET | `/healthz`, `/readyz`, `/metrics` | Probes, Prometheus | `readyz` checks the DB |

- Errors are RFC 9457 `application/problem+json` with stable `type` URIs.
- Auth: a single operator bearer token from env/secret (the API binds to localhost or a private network). OIDC is out of scope.
- The OpenAPI document is exported in CI and generates the console client types.

---

## 19. Storage choices

- **Parquet + DuckDB** for every experimental table. DuckDB reads Parquet in place (local path or `gs://` via httpfs) for the API and reports.
- **PostgreSQL** only for jobs and state (Day 7).
- **GCS** (cloud) mirrors the `artifacts/` layout: `gs://<project>-ct-artifacts/` (versioning on, lifecycle 30 days → Nearline for PCAP) and `gs://<project>-ct-restricted/` (separate IAM).
- **Excluded until a measured need appears:** BigQuery, Pub/Sub, MinIO, Cloud SQL, Redis.

---

## 20. Cloud path (Day 8)

### 20.1 Topology (Compute Engine)

```text
 client VM (c3-standard-4, zone a)                 GPU VM (g2-standard-8, 1× L4, zone b, same region)
 ├─ loadgen + capture (compose)   ══TLS:8443══►   ├─ shaper (+netinit)  ──HTTP──►  vllm (7B model)
 └─ uploads artifacts → GCS                        └─ optional capture on NIC (V-S vantage)
 firewall: 8443 only from client VM (and optional operator IP for the P1 Internet-path arm); SSH via IAP only
```

### 20.2 Specifics

- **Models:**
  - Local: `Qwen/Qwen2.5-1.5B-Instruct`.
  - Cloud: `Qwen/Qwen2.5-7B-Instruct`, bf16, pinned revision, fits 24 GB with KV headroom for C ≤ 32; confirmed in the Day-8 saturation test. Both are Apache-2.0 and ungated.
- **Images:**
  - GPU VM uses the Deep Learning VM image (NVIDIA driver preinstalled) with Docker and the NVIDIA Container Toolkit.
  - Images are pulled from Artifact Registry by digest. Weights are downloaded at boot to a local SSD/PD cache (or restored from a GCS model cache bucket).
- **Identity:** each VM has an attached service account with least privilege (client: write artifacts, write restricted; GPU VM: read model cache, write artifacts/server-capture). There are no SA keys. CI uses WIF.
- **NLB (P1):** a regional external passthrough NLB in front of the GPU VM demonstrates production ingress without TLS termination. Responses use DSR, so the observation model is unchanged.
- **Cost guardrails:**
  - Budget with alerts at 50/80/100%.
  - VM labels for attribution.
  - `make cloud-stop` stops the GPU VM. A guest `shutdown -h +240` safety timer is armed on every boot. The GPU VM runs only during campaigns.
  - Estimated campaign: ≤ 8 GPU-hours plus a client VM, i.e. **≈ $10–15** at on-demand prices. The rate is UNVERIFIED and must be checked in the console before apply.
- **GKE (P2):** a GKE Standard deployment (not Autopilot, because node networking and offloads are not controllable there) as an operations demonstration and for autoscaling-as-confounder studies. It is **not** the measurement baseline.

---

## 21. Security

| Area | Control |
|---|---|
| Secrets | None in git (gitleaks pre-commit + CI). Lab CA/keys generated into `.secrets/` (gitignored). Cloud TLS key generated on the VM at boot, or held in Secret Manager (P1). No HF token needed (ungated models) |
| Identity | GitHub → GCP via Workload Identity Federation. VMs use attached service accounts. No long-lived keys |
| Least privilege | Per-VM service accounts. The analysis identity cannot read `ct-restricted` (403 test). Terraform-managed firewall; SSH via IAP |
| Containers | Non-root (uid 10001), `read_only: true`, `no-new-privileges`, `cap_drop: ALL`. Exceptions: capture (`NET_RAW`, `NET_ADMIN`) and netinit (`NET_ADMIN`, exits after setup), documented in ADR-004. Minimal slim bases pinned by digest |
| Supply chain | `uv.lock`/`pnpm-lock.yaml` committed; Renovate/Dependabot; osv-scanner (or pip-audit + pnpm audit); Trivy (fs + images, HIGH/CRITICAL gate with an allow-list); hadolint; Semgrep (SAST); Syft SBOM per image; Cosign keyless signing in `images.yml` |
| Threat-model enforcement | §14.3 guards; canary test; oracle endpoints need operator scope |
| API | Bearer token; request size limits; RFC 9457 errors without stack traces |

---

## 22. Observability

- **Logs:** structlog JSON to stdout with `ts, level, component, event, session_id, request_id (when applicable), arm, stage, job_id`. A lint rule plus a test ensure no prompt or response text is ever logged.
- **Metrics:** Prometheus endpoints on the shaper (§11.5), loadgen (requests by status, client TTFT/ITL histograms, lateness self-check), capture supervisor (capture state, drops), worker (jobs by state and duration), API, and vLLM (scraped).
- **Dashboards:** compose profile `obs` runs Prometheus + Grafana with two dashboards: "Session live" (shaper, loadgen, vLLM) and "Pipeline" (jobs, capture, validation).
- **Tracing:** OpenTelemetry only in the API and worker (stage spans; `traceparent` propagated in job payloads). No spans in the shaper hot path.
- **Health:** `/healthz` and `/readyz` on every long-lived service; compose `healthcheck` + `depends_on: condition: service_healthy`.

---

## 23. Docker

| Image | Base (pinned digest) | Notes |
|---|---|---|
| `crosstalk-shaper` | build: `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`; runtime: `python:3.13-slim-bookworm` | Installs only the shaper extra. Non-root, RO rootfs. `EXPOSE 8443 9100` |
| `crosstalk-loadgen` | same | Also runs the `collect` worker |
| `crosstalk-capture` | `debian:bookworm-slim` + `tshark` (dumpcap) + `ethtool` + Python venv (capture supervisor) | Non-root in group `wireshark`; `NET_RAW`/`NET_ADMIN` via compose `cap_add` |
| `crosstalk-netinit` | `debian:bookworm-slim` + `ethtool` + `iproute2` | Runs once and exits |
| `crosstalk-worker` | Python runtime + ML extra | One image, deployed as two services: `worker-attack` (queue `attack`; observer-only mounts) and `worker-evaluate` (queue `evaluate`; oracle read-only, no restricted) per §14.3 |
| `crosstalk-api` | Python runtime + API extra | — |
| `crosstalk-console` | `node:24-bookworm-slim` build → minimal Node runtime | Next.js standalone output |
| `vllm` | `vllm/vllm-openai:v0.29.0@sha256:…` | External |

All images are multi-stage, carry OCI labels (`revision`, `source`, `created`), include no build tools or secrets in final layers, and have `HEALTHCHECK` where meaningful.

---

## 24. Terraform

- **Layout:** `infra/terraform/modules/{network, gpu_server, client, storage, registry, iam, budget, nlb}`, `envs/dev/{main.tf, variables.tf, outputs.tf, backend.tf, terraform.tfvars.example}`.
- **State:** GCS backend (bucket created once by `infra/bootstrap.sh`; versioning on).
- **Conventions:**
  - Labels `{app=crosstalk, env, owner, campaign}` on every resource.
  - `prevent_destroy` on the artifacts buckets.
  - Variables for machine type, GPU count, zone, model, and budget.
  - `terraform fmt/validate` plus tflint in CI; `plan` on PRs via WIF.
  - `apply` is manual (`workflow_dispatch` with environment protection) or local.
- **Outputs:** VM names and internal/external IPs, bucket names, Artifact Registry repo, service account emails.

---

## 25. CI/CD (GitHub Actions)

| Workflow | Trigger | Jobs (blocking unless noted) |
|---|---|---|
| `ci.yml` | PR, push | `quality` (ruff check + format --check, pyright, import-linter) · `unit` (pytest unit + property, coverage report) · `lab-integration` (ubuntu runner: compose `lab.yml` with the **mock upstream**, capture + reconstruct + tshark agreement + fidelity + client-compat tests with openai-python/openai-node) · `console` (pnpm lint, typecheck, test, build) · `terraform` (fmt, validate, tflint) · `security` (gitleaks, semgrep, osv-scanner, trivy fs, hadolint) |
| `images.yml` | push to `main`, tags | Buildx build → Trivy image scan (gate) → Syft SBOM → Cosign keyless sign → push by digest to GHCR (Artifact Registry after Day 8) |
| `infra.yml` | PR touching `infra/`, manual | WIF auth → `terraform plan` (PR comment); manual `apply` |
| `bench.yml` | manual, nightly (non-blocking) | Mock-upstream shaper benchmark; results artifact, compared to baseline for regressions |

Branch protection on `main`: PR required, all blocking jobs green, no force-push.

---

## 26. Test strategy

| Level | Scope and examples |
|---|---|
| **Unit** | SSE parser (spec vectors); framer exact-size (exhaustive T); policies with a fake clock (golden emission schedules); pcapng/pcap reader (tshark-generated fixtures); TCP reassembly; TLS header parser; flow join; features (golden vectors); splits; bootstrap (known-variance synthetic data); Holm |
| **Property (Hypothesis)** | Content fidelity for every policy × arbitrary upstream chunking; framer never exceeds 16,384 and hits targets exactly; any segmentation or reordering of a byte stream reassembles identically; any record sequence split across segments parses identically; family-disjointness of splits for arbitrary label tables |
| **Contract** | Shaper black-box HTTP/TLS contract (`tests/shaper_contract/`, language-agnostic); Parquet schemas vs `contracts/tables.py`; OpenAPI snapshot; JSON Schema of configs |
| **Client compatibility** | openai-python and openai-node streaming against the shaper for every arm: content identical, chunk count matches the merged/unmerged expectation, no errors; WHATWG parser vectors |
| **Integration (lab)** | 50–200-request sessions through compose: offload assertions, zero drops, 100% tshark agreement, ≥ 99.99% emission fidelity, D1 bucket sizes exact on the wire, D3 record sizes constant and ticks within lateness bounds |
| **ML leakage** | Oracle-column absence; the canary string never appears in observer/features/models; `Pipeline` fitted only on training indices (spy); null control at chance on synthetic data; injected-signal dataset recovered; adaptive training reads only training-family traces |
| **Failure injection (Day 9)** | Kill shaper mid-session → affected requests `failed`, session `PARTIAL`, never `VALID`. Kill worker mid-job → lease expiry → retry. netem loss/reorder → flows flagged, not misparsed. Slow client → bounded memory + abort. Upstream timeout → clean SSE error. Postgres restart → jobs resume |
| **Load/soak** | Shaper at the validated N for 60 min: no leaks (fd, tasks, RSS), lateness stable |

**Coverage floors:** `capture/`, `shaper/framer.py`, `shaper/policies/` and `stats/` ≥ 90% lines; overall ≥ 75%.

---

## 27. Benchmarks (tracked in `bench/RESULTS.md`, JSON in artifacts)

| ID | What | Metrics | When |
|---|---|---|---|
| B1 | Runtime gate (§3.2) | Record fidelity; lateness p50/p99/p99.9/max; relay Δ; CPU; RSS; backpressure | Day 1 |
| B2 | Shaper per arm (mock upstream with a real token-timing trace) | Queue delay and timer lateness p50/p95/p99/p99.9; emissions/s; CPU s per request; RSS; loop lag; stalls | Day 4–5, nightly |
| B3 | Capture + reconstruction | Max pps without drops; parser packets/s and MB/s; tshark agreement time | Day 2 |
| B4 | vLLM saturation (local and L4) | TTFT/ITL p50/p99, tokens/s, queue time vs C ∈ {1, 2, 4, 8, 16, 32, 64} | Day 6, Day 8 |
| B5 | End-to-end per session | Bandwidth per request and arm; client TTFT/ITL deltas vs a0; shaper CPU | Every session (automatic) |

---

## 28. Failure engineering

| Failure | Detection | Response | Data integrity |
|---|---|---|---|
| vLLM down/slow | Upstream connect/first-byte timeout; `/readyz` false | SSE error to client; request `failed`; loadgen retries a slot at most once (logged) | Failed slots excluded; > 2% failures → session `PARTIAL` |
| Shaper crash | Loadgen connection errors; container restart count | Session `PARTIAL`; resume planned slots only after restart **and** re-validation | Never merge pre- and post-crash data without a flag |
| Capture drops / dumpcap exit | `capture.json` counters; supervisor exit code | Session `INVALID` | Excluded from results |
| Incomplete flows / parse desync | Reconstruction anomalies | Affected requests excluded; > 1% → `INVALID` | Listed in validation JSON |
| Worker crash | Lease expiry | Re-queue with backoff; `dead` after 5 attempts | Idempotent stage keys; atomic outputs |
| Postgres unavailable | Connection errors | Workers back off; API `readyz` false (503) | No partial state transitions (transactions) |
| Disk full | Pre-flight free-space check (≥ 2× expected PCAP size) | Refuse to start the session | — |
| GPU VM preempted/stopped | Missing heartbeats | Campaign job `failed`; resume by session | Sessions are atomic units |
| Budget threshold | Billing alert | Operator stops VMs (runbook) | — |

---

## 29. Global quality gates (all must pass for a result to be reportable)

**Networking**
- TLS-record parser equals tshark on 100% of records in the validation corpus, including netem loss/reorder fixtures.
- Packet/offload behaviour documented per session (`capture.json`, offload snapshots, `max_ip_len ≤ MTU` for packet features).
- No code or document assumes write = record. Emission → record fidelity is measured per session (≥ 99.99% in the baseline).
- Capture completeness: zero drops and ≤ 1% excluded flows per reportable session.
- PCAP retained for every reportable session.
- Protocol profile recorded (ADR-002 arm id).

**Shaper**
- Decoded-content fidelity is 100% across property tests and client-compat tests.
- Bounded buffers (per-stream caps enforced in tests); graceful disconnect; slow-client backpressure (G5); upstream timeout handling.
- Memory ceiling (G4); concurrency benchmark (B2).
- Timer lateness and loop lag measured and reported per session.
- Instrumentation overhead quantified: B2 with the oracle log on vs off, Δp99 ≤ 0.2 ms.

**ML**
- Prompt-family-disjoint outer and inner folds (asserted).
- Matched regimes reported next to NAT.
- Null labels at chance (20 permutations).
- Grouped paired bootstrap CIs on every metric and Δ.
- No test leakage into scaling, selection, early stopping or thresholds.
- Adaptive training only on training families.
- Fixed provenance (manifest).

**Security**
- The attack path cannot read plaintext or oracle data (loader guard, import-linter, mounts, 403 test, canary).
- No secrets in the repository.
- Security scanners green.
- Minimal privileges and hardened containers.
- Signed images with SBOMs (release).

**Reproducibility:** every reportable session and experiment has the §14.4 manifest with `git_dirty=false`.

**Performance:** reported per arm and condition with n:
- p50/p95/p99 (p99.9 when n ≥ 10,000 samples) TTFT and inter-token latency;
- CPU, RSS, throughput, failed/dropped requests;
- added latency vs a0, timer lateness, bandwidth overhead.

---

## 30. Ten-day plan

Assumptions:
- One developer with AI coding agents, and the local GPU workstation.
- Collections run in the background and overnight while code is written.
- Each day ends with CI green and a committed artifact.
- "Drop if delayed" items move to the post-Day-10 backlog (§31) without breaking later days.

**Collection budget and schedule (estimated; re-measured on Day 3).** With the 1.5B model at C=1, a request takes about 2.5–3.5 s, so 1,200 requests (200/class) are ≈ 1 h per arm at C=1. Higher concurrency is much faster per request.

**Data-path freeze.** At the end of Day 5 the data path is tagged `data-v1`: shaper, loadgen, capture, reconstruction, workload version, vLLM image and model revision. Only sessions collected with `data-v1` are reportable. A later change to any of those modules requires a new tag and re-collection of the affected sessions. Analysis code can keep changing until Day 10.

| When | Collection (all `data-v1`) | GPU time |
|---|---|---|
| Day 3 (pre-freeze) | E0 pilot + E0 regimes. Development results only | ≈ 3.5 h |
| Night 5→6 | **E1**: 5 arms (+D1r if ready) at C=1; 2 sessions × 100/class/arm | ≈ 6–7 h |
| Day 6 | **E2 replicate 1**: time-alternating blocks C1/C8/C32; 5 arms + `stream_interval=4` for a0/a2; 100/class/arm/level | ≈ 4 h |
| Night 6→7 | **E2 replicate 2** (same plan, different seed and time) | ≈ 4 h |
| Night 7→8 | **E0 final** (NAT, MATCH-P, MATCH-PR at 200/class), driven through the new API | ≈ 3 h |
| Day 8 | Cloud mini-campaign (L4, 7B) | ≈ 2–3 h |
| Night 9→10 | **Reproducibility replicate** via the API: a0/a1/a3 at C=1, 100/class | ≈ 1 h |

Day 10 re-analyses all VALID `data-v1` sessions with frozen analysis code. It does not re-collect everything, which would not fit in one night.

### Day 1 — Architecture freeze + foundation + runtime gate

- **Objective:** a clean repository with production tooling, frozen P0 architecture (ADRs), typed contracts, a working lab host, and the runtime decision confirmed.
- **Exact tasks:**
  1. Scaffold the repo per §5:
     - `pyproject.toml` (uv workspace; extras `shaper`, `loadgen`, `capture`, `ml`, `api`; dev group); `.python-version`; ruff/pyright/pytest/import-linter configs; `Makefile`; pre-commit (ruff, gitleaks, end-of-file).
     - `.github/workflows/ci.yml` (quality, unit, security, terraform fmt, console placeholder).
  2. `src/crosstalk/common/`: settings, JSON logging, ULIDs, error taxonomy (`CrosstalkError` → `ConfigError`, `CaptureError`, `ReconstructionError`, `ValidationError`, `UpstreamError`, `OracleAccessError`), atomic writes, sha256 helpers.
  3. `src/crosstalk/contracts/`: `ExperimentConfig`, `SessionPlan`, `PolicyConfig`, `CaptureConfig`, `RunManifest`; pyarrow schemas for every §14.2 table; JSON Schema export command.
  4. Write ADR-001…013 (§4) in `docs/adr/`.
  5. Rebuild the Day-0 probe (RESEARCH §11.3) in `bench/runtime/` (Python, Node; Java/Netty stub only). Run gate B1 on the lab host and record `bench/RESULTS.md` + ADR-003 status.
  6. Lab host: Docker Engine + NVIDIA toolkit in WSL2/Linux. `docker run --gpus all vllm/vllm-openai:v0.29.0 --model Qwen/Qwen2.5-1.5B-Instruct` streams one completion. Capture image builds; `ethtool -K` works on a veth inside a compose netns.
  7. Dockerfile skeletons for shaper, loadgen, capture, netinit (non-root, RO); `deploy/compose/lab.yml` skeleton with a mock-upstream profile.
  8. **Lead-time items:** create the GCP project, billing budget and alert; request L4 GPU quota in two regions; create the GitHub repository settings (branch protection).
- **Files/modules touched:** `pyproject.toml`, `uv.lock`, `Makefile`, `.github/workflows/ci.yml`, `src/crosstalk/{common,contracts,cli}/`, `docs/adr/*`, `bench/runtime/*`, `bench/RESULTS.md`, `services/*/Dockerfile`, `deploy/compose/lab.yml`.
- **Dependencies:** none (first day). External: Docker, NVIDIA driver, internet access for images and models.
- **Tests:** contracts round-trip (Pydantic ↔ JSON Schema, pyarrow schema instantiation); import-linter passes on the skeleton; error taxonomy unit tests.
- **Benchmarks:** B1 (G1–G6 where G5/G6 run against the probe relay; full G5/G6 re-run on Day 4 against the real shaper).
- **Acceptance criteria:**
  - `uv sync --locked && make lint typecheck test` passes; CI green on the first PR.
  - ADRs merged; gate results committed with a decision.
  - vLLM streams on the local GPU, **or** ADR-013 fallback recorded.
- **Failure conditions:**
  - Python+uvloop fails G1–G4 → switch per the §3.2 decision rule (Node, then Netty). The shaper implementation on Days 2/4 follows the chosen runtime; nothing else changes.
  - vLLM cannot run locally → mock upstream locally plus cloud L4 pilots from Day 3 (Day 8 work pulled forward partially).
- **Drop if delayed:** Java/Netty stub; console placeholder job; JSON Schema export command.
- **End-of-day artifact:** tagged commit `day1` with CI green, ADRs, `bench/RESULTS.md` (gate table), lab smoke log.
- **Research question answered:** *Can the chosen runtime implement timing defenses with deterministic record boundaries and small, measured overhead?* (the precondition for Contribution A).

### Day 2 — Traffic laboratory

- **Objective:** our own independently validated encrypted LLM trace, with all three layers aligned.
- **Exact tasks:**
  1. **Shaper D0 minimal:** TLS server per §7/§11 (h11 parsing, aiohttp upstream, SSE parser, D0 passthrough, one `write()` per event, terminator, graceful shutdown, admin `/healthz` `/readyz`), plus the buffered oracle emission log.
  2. **Loadgen:** asyncio + uvloop + h11 TLS client, one connection per request, sockname capture, per-read and per-event timestamps, usage parsing, restricted text writer, capture control client. Validation mode adds the MemoryBIO record oracle.
  3. **Capture container:** supervisor API (start/stop), offload disable + snapshot, dumpcap command per §8, `capture.json`.
  4. **`crosstalk.capture`:** pcapng + pcap reader, TCP reassembly, TLS record parser, phase labelling, flow join, Parquet writers (§10.1).
  5. **tshark validation harness** and keylog validation (validation sessions only), emission→record fidelity report.
  6. **`experiments/validation.yaml`:** 200 requests, D0, C ∈ {1, 16}, plus one session with netem loss 1% / reorder 5% on the loadgen interface.
  7. **First waterfall:** `crosstalk report waterfall` renders emissions / records / packets for one request (PNG) — the Day-2 demo artifact.
  8. Write `docs/experiments/day2-validation.md` covering V1–V5: emission→record, record↔packet coalescing statistics, parser ≡ tshark, offload check, completeness.
- **Files/modules touched:** `src/crosstalk/{shaper,loadgen,capture,traces}/`, `services/{shaper,loadgen,capture,netinit}/`, `deploy/compose/lab.yml`, `experiments/validation.yaml`, `tests/{unit,property,integration,lab}/`, `docs/experiments/day2-validation.md`.
- **Dependencies:** Day 1 contracts, lab host, runtime decision.
- **Tests:**
  - Unit: reader fixtures, reassembly (OOO, retransmission, overlap conflict, holes), TLS parser, framer (D0 path), SSE parser.
  - Property: arbitrary segmentation → identical records.
  - Lab integration: offloads off, zero drops, tshark 100%, fidelity ≥ 99.99%.
- **Benchmarks:** B3 (capture pps without drops; parser throughput).
- **Acceptance criteria:**
  - Validation sessions `VALID`.
  - 100% parser/tshark agreement (including netem); ≥ 99.99% emission fidelity at C=1 and C=16.
  - `max_ip_len ≤ 1500` with offloads off.
  - Waterfall PNG committed.
- **Failure conditions:**
  - tshark disagreement → fix the parser before anything else. No Day-3 collection on an unvalidated parser.
  - Drops at C=16 → raise the dumpcap buffer or lower C, and record the limit.
- **Drop if delayed:** keylog validation (keep tshark agreement); netem session; packet-layer tables beyond `packets.parquet`.
- **End-of-day artifact:** a VALID session directory + `day2-validation.md` + waterfall.
- **Research question answered:** *What does an on-path observer actually see from a vLLM stream, and do our three layers map onto each other as measured?*

### Day 3 — Attack baseline (E0)

- **Objective:** an honest E0 baseline, naturalistic vs matched, with contamination controls.
- **Exact tasks:**
  1. **Workload corpus v1** (§13): authoring YAML, builder, tokenizer accounting, banned-pattern check, `workload@1.0.0` artifact + hash; regimes NAT/MATCH-P/MATCH-PR; determinism set.
  2. **Session planner** (block randomization, fixed-width headers) and executor integration.
  3. **Pilot collection:** NAT D0 C=1, 60 requests/class, to calibrate timing and power. Then **development E0 collections:** NAT, MATCH-P and MATCH-PR at 200/class each, D0, C=1, running in the background while coding. These are pre-freeze. The reportable E0 is re-collected under `data-v1` on Night 7→8.
  4. **Features** R1/R2 (+ T-len subset); **attacks** A0/A1/A2 with nested grouped CV; predictions store.
  5. **Stats:** cluster bootstrap, null control (20 permutations), noise floor, power check (§16), learning curve.
  6. `crosstalk.evaluation.e0` produces `experiments/<e0>/report.md` + figures.
- **Files/modules touched:** `workloads/corpus/v1/`, `src/crosstalk/{workloads,features,attacks,stats,evaluation}/`, `experiments/e0.yaml`, `tests/{unit,property,ml_leakage}/`.
- **Dependencies:** Day-2 validated pipeline.
- **Tests:** split disjointness (property); scaler/selection fitted inside folds (spy); injected-signal synthetic dataset recovered; null control at chance on synthetic data; MATCH-PR realized `completion_tokens == L` (session QA); request-size parity per class in MATCH-P.
- **Benchmarks:** per-request collection time and GPU throughput at C=1 (feeds the Day-5/6 schedule).
- **Acceptance criteria:**
  - E0 report with balanced accuracy, macro-F1 and PR-AUC with cluster-bootstrap CIs for NAT, MATCH-P and MATCH-PR; chance and null-control rows; T-len diagnostic.
  - Noise-floor table.
  - Power-check decision (n per class for E1/E2) recorded.
- **Failure conditions:**
  - Null control above chance → stop and find the leak (usually split or time-as-label).
  - MATCH-PR length forcing not exact → fix sampling params before E1.
- **Drop if delayed:** MATCH-P (keep NAT + MATCH-PR); learning curve (move to Day 10).
- **End-of-day artifact:** E0 report (pilot-scale if full collections are still running; final numbers regenerated overnight).
- **Research question answered:** *RQ0: is leakage reproducible on our stack, and how much is "just length"?*

### Day 4 — Streaming shaper defenses

- **Objective:** real in-path defenses D1 and D2, deployment-compatible, validated on the wire.
- **Exact tasks:**
  1. Policy interface + emitter (timers via `call_at`, backpressure per §11.2, stall timeout) + framer (exact-size padding, merge, dummy) + accounting and metrics.
  2. D1 bucket padding (ladder from the E0 pilot's event-size distribution) and D2 window batching; lab-mode arm selection (`x-ct-arm`).
  3. **Compatibility suite:** openai-python + openai-node + WHATWG vectors against every arm (`tests/compat/`).
  4. **Wire validation session** (100 requests per arm, interleaved): D1 records equal bucket sizes exactly; D2 emission times on the window grid; content fidelity 100%.
  5. B2 benchmark per arm (mock upstream replaying a real vLLM token timeline); G5/G6 re-run on the real shaper.
  6. D1r random padding (P1) if time allows.
- **Files/modules touched:** `src/crosstalk/shaper/{emitter,framer,policies/*,accounting,admin}.py`, `tests/{property,compat,lab,shaper_contract}/`, `bench/shaper/`.
- **Dependencies:** Day-2 shaper; Day-3 event-size distribution.
- **Tests:** framer exhaustive sizes; fidelity property (10,000 streams × arms); fake-clock golden schedules; client-compat matrix; lab wire checks.
- **Benchmarks:** B2 (queue delay, timer lateness, CPU/request, RSS, loop lag) per arm at C ∈ {1, 16, 64}.
- **Acceptance criteria:** fidelity 100%; compat suite green for D0–D2; bucket exactness 100% of padded records; D2 grid lateness p99 ≤ 2.5 ms at C=16; G5/G6 pass.
- **Failure conditions:**
  - Any SDK yields spurious chunks → the arm is marked experimental-only and the padding placement is fixed.
  - Lateness exceeds the gate → enable multi-process mode, or cap C for E2.
- **Drop if delayed:** D1r; admin `/policies` endpoint; Grafana dashboard.
- **End-of-day artifact:** a wire-validation session report for D0–D2 + `bench/RESULTS.md` B2 table.
- **Research question answered:** *Can a deployable shaper change the observed record sequence exactly as specified, at measured cost?*

### Day 5 — Strong timing defense + adaptive attacker

- **Objective:** D3 constant-rate and D4 hybrid deployed; the first static-vs-adaptive result.
- **Exact tasks:**
  1. D3 per §12.2 (start on request, fixed record size, dummy comment blocks, tail quantization). D4 = D2 + D1.
  2. Wire validation for D3: all response records equal `record_size + 17`; inter-record gaps within lateness bounds; tick count ≡ 0 mod K.
  3. `attacks/protocols.py`: static/adaptive (and transfer stub) on shared family folds; `stats/paired.py`: paired Δ and ρ with CIs; Holm; E1 report generator.
  4. E1 **pilot session** (60/class/arm, all arms interleaved, C=1) → first static-vs-adaptive table.
  5. Freeze the E1 default parameters in the manifest and **tag `data-v1`** (§30 freeze rule). Launch the **full E1 collection** overnight: 2 sessions × 100/class/arm, 5 arms (+D1r if ready), C=1.
- **Files/modules touched:** `src/crosstalk/shaper/policies/{constant_rate,hybrid}.py`, `src/crosstalk/{attacks,stats,evaluation}/`, `experiments/e1.yaml`.
- **Dependencies:** Day-4 policy framework; Day-3 ML pipeline.
- **Tests:** D3 termination bound and constant size (property); adaptive-training-reads-only-training-families test; paired bootstrap correctness on synthetic paired data (coverage ≈ 95%).
- **Benchmarks:** B2 for D3/D4 (dummy rate, bandwidth overhead, queue delay vs generation speed).
- **Acceptance criteria:** D3 wire checks pass; pilot E1 table with static and adaptive rows for every arm, Δ and ρ with CIs; E1 collection running with a manifest.
- **Failure conditions:**
  - D3 lateness breaks fixed spacing → increase T or scale out processes, and record it.
  - Adaptive results unstable across seeds → increase n or reduce classes (documented).
- **Drop if delayed:** transfer setting; D1r in E1 (keep 5 arms).
- **End-of-day artifact:** `experiments/<e1-pilot>/report.md` with the first static-vs-adaptive comparison.
- **Research question answered:** *RQ1 (pilot): how much leakage survives each defense against static vs adaptive attackers, and at what cost?*

### Day 6 — Serving conditions (E2)

- **Objective:** the first E2 result: leakage and defense cost vs load and server-side aggregation.
- **Exact tasks:**
  1. B4 saturation curve locally (C ∈ {1, 2, 4, 8, 16, 32, 64}) → pick E2 levels (default {1, 8, 32}; cap at the G2/G3-validated load).
  2. E2 sessions (`data-v1`):
     - **Replicate 1 during the day, replicate 2 overnight.**
     - Each session alternates concurrency blocks C1 → C8 → C32 (repeated), drained between blocks (§16), with all arms interleaved inside every block.
     - `stream_interval ∈ {1, 4}` randomized per request for a0 and a2.
     - vLLM metrics scraped per block (queue time, running/waiting, ITL).
  3. Analyse the overnight E1 data (full report) and E2 (ρ_adaptive vs C per arm with two-stage bootstrap; cost vs C: TTFT/ITL percentiles, bandwidth, shaper CPU, dummy rate).
  4. Frontier computation (`evaluation/frontier.py`): cost axis vs ρ (static/adaptive).
- **Files/modules touched:** `experiments/e2.yaml`, `src/crosstalk/evaluation/{e1,e2,frontier}.py`, `bench/serving/`.
- **Dependencies:** D0–D4 frozen; E1 collection finished.
- **Tests:** session-order alternation asserted by the planner; per-request `stream_interval` honored (vLLM usage/chunk counts); two-stage bootstrap unit tests.
- **Benchmarks:** B4; B5 per session.
- **Acceptance criteria:** E1 full report (8 Holm-corrected primary tests); E2 report from replicate 1 (5 primary tests; two-replicate analysis follows once replicate 2 lands); frontier data file; all sessions `VALID`.
- **Failure conditions:**
  - Capture drops at C=32 → reduce C to the validated level (documented).
  - GPU OOM → lower `max-num-seqs` or the model size (documented).
- **Drop if delayed:** the `stream_interval` factor (keep concurrency); the second replicate session at the highest C.
- **End-of-day artifact:** `experiments/<e1>/report.md`, `experiments/<e2>/report.md`, `frontier.parquet`.
- **Research question answered:** *RQ2: does a defense that looks effective at concurrency 1 remain effective, and at what cost, under load?*

### Day 7 — Durable experiment orchestration

- **Objective:** one API call or config triggers the full pipeline, durable and resumable.
- **Exact tasks:**
  1. Postgres (compose `platform.yml`), Alembic migrations for `experiments/sessions/jobs/artifacts/events`.
  2. Job queue (`SKIP LOCKED`, leases, heartbeat, reaper, backoff + jitter, dead state); the `collect` worker inside the loadgen container; `worker-attack` and `worker-evaluate` services with their §14.3 mounts.
  3. Stage registry with idempotent keys and artifact registration (sha256); session state machine; cancellation.
  4. FastAPI `/v1` per §18 with RFC 9457 errors, Idempotency-Key, cursor pagination, operator token; OpenAPI export.
  5. Runbooks: `docs/runbooks/{restart-worker,resume-session,invalid-session}.md`.
  6. **Night 7→8:** launch the reportable **E0 final** collection (NAT, MATCH-P, MATCH-PR at 200/class, `data-v1`) through the API. This is the first real API-driven campaign.
- **Files/modules touched:** `src/crosstalk/{orchestration,api}/`, `deploy/compose/platform.yml`, `services/{worker,api}/`, migrations, `tests/integration/`.
- **Dependencies:** Day 2–6 stage functions (unchanged; wrapped).
- **Tests:** concurrent claim (two workers, one job); lease expiry → retry; idempotent re-run skips completed stages; kill worker mid-attack → resumes; Idempotency-Key replay returns the same response; API contract tests.
- **Benchmarks:** end-to-end E0 (small) via the API; job-claim latency; API p95.
- **Acceptance criteria:** `POST /v1/experiments` + `POST /runs` executes collect → report for a small E0 with no manual step; kill tests pass; OpenAPI committed.
- **Failure conditions:** lease/heartbeat races → fall back to a single analysis worker (keep durability) and fix after Day 10.
- **Drop if delayed:** cancellation of running jobs (keep queued-job cancel); events table.
- **End-of-day artifact:** a recorded API-driven run (manifest + job history).
- **Research question answered:** none new. This day makes E0–E2 **reproducible by construction** (a precondition for any claim).

### Day 8 — Cloud deployment

- **Objective:** a real GCP GPU deployment running a mini-campaign with the production architecture.
- **Exact tasks:**
  1. **Terraform** (`infra/terraform`): network (VPC, subnet, firewall, NAT, IAP SSH), `gpu_server` (g2-standard-8, L4, DLVM image, startup script → compose shaper + netinit + vllm 7B), `client` (c3-standard-4, compose loadgen + capture), storage (artifacts + restricted buckets, lifecycle, versioning), registry, IAM (per-VM SAs, least privilege, WIF for GitHub), budget alerts; optional NLB (P1).
  2. `images.yml` pushes signed images to Artifact Registry. VMs pull by digest.
  3. Cloud validation: offload control on the VM NICs (record whatever is fixed), capture on the client VM (V-C) **and** the GPU VM NIC (V-S), tshark agreement, emission fidelity.
  4. B4 saturation on L4 (7B) to choose C.
  5. **Mini-campaign:** E0 NAT (D0) + E1 subset {a0, a1, a3} at C=8, 100/class/arm. Artifacts to GCS. Analysis runs locally or on the client VM.
  6. Vantage comparison report: records identical V-C vs V-S; packet-level differences quantified.
  7. `make cloud-stop`; cost report.
- **Files/modules touched:** `infra/terraform/**`, `deploy/gce/**`, `.github/workflows/{images,infra}.yml`, `experiments/cloud-mini.yaml`, `docs/runbooks/cloud.md`.
- **Dependencies:** Days 2–7; GPU quota for L4 in the chosen region (request on Day 1).
- **Tests:** `terraform validate`/tflint; post-apply smoke (health endpoints, one streamed request); the analysis identity gets 403 on the restricted bucket.
- **Benchmarks:** B4 (L4); B5 per cloud session; campaign cost.
- **Acceptance criteria:**
  - `terraform apply` from empty succeeds and `destroy` leaves only the protected buckets.
  - Mini-campaign sessions `VALID`, with a cloud E0/E1-subset report and a vantage comparison.
  - Spend within budget.
- **Failure conditions:**
  - No L4 quota or capacity → alternate region, or T4 (`n1-standard-8` + T4) with the 1.5B–3B model (documented).
  - NIC offloads fixed on → packet features flagged; the record layer is still valid.
- **Drop if delayed:** NLB; V-S capture; Artifact Registry (pull from GHCR instead).
- **End-of-day artifact:** `experiments/<cloud-mini>/report.md`, Terraform plan/apply logs, cost note.
- **Research question answered:** *Do the E0/E1 conclusions hold on a production-class GPU and model, and how do client-side and server-side vantages differ?*

### Day 9 — Results UI + hardening

- **Objective:** a demo-grade system, reliable under failure, with the four research screens.
- **Exact tasks:**
  1. **Console (Next.js + TS strict)** with four screens:
     - Runs/experiments overview.
     - **Trace Explorer:** three-layer waterfall, the oracle layer toggle clearly labelled, canvas/uPlot.
     - **Attack Results:** metrics with CIs, confusion matrix, null-control badge, static/adaptive rows.
     - **Leakage-vs-Cost Frontier:** static/adaptive toggle, cost-axis selector, concurrency selector.
     - Generated API types; zod validation of responses.
  2. API result endpoints backed by DuckDB over Parquet.
  3. **Hardening:**
     - Soak (60 min at the validated C).
     - Failure-injection suite (§26).
     - Trivy/Semgrep/gitleaks clean.
     - Container security review (caps, RO rootfs, users); plaintext-access tests (loader guard, mounts, 403, canary).
     - Runbooks.
  4. **Night 9→10:** reproducibility replicate via the API (a0/a1/a3, C=1, 100/class). Day 10 compares it with E1 within the measured noise.
- **Files/modules touched:** `apps/console/**`, `src/crosstalk/api/routers/results.py`, `tests/{failure,e2e}/`, `docs/runbooks/*`.
- **Dependencies:** Day-6/8 result tables; Day-7 API.
- **Tests:** Playwright smoke on the four screens; API contract; failure-injection assertions.
- **Benchmarks:** soak (RSS/fd/task stability); API p95 for results endpoints.
- **Acceptance criteria:** the four screens render real experiments; the soak shows no growth beyond 10%; every failure scenario ends in its documented state; scanners green.
- **Failure conditions:** UI time overrun → ship Trace Explorer + Frontier only (Attack Results via the report markdown).
- **Drop if delayed:** config comparison view; dark theme polish; Grafana dashboards.
- **End-of-day artifact:** demo-ready build + hardening report (`docs/experiments/day9-hardening.md`).
- **Research question answered:** none new. This day makes the evidence inspectable and the system trustworthy.

### Day 10 — Final campaign + reproducibility bundle

- **Objective:** a finished project with frozen, reproducible results.
- **Exact tasks:**
  1. **Freeze:** tag `v1.0.0`; record image digests, `uv.lock`, vLLM version, model revisions, `workload@1.0.0`, experiment configs, policy-set hashes.
  2. **Final evidence set:** every VALID `data-v1` session, analysed with the frozen analysis code:
     - E0 final (Night 7→8).
     - E1 (Night 5→6, 2 sessions).
     - E2 (2 replicates, Day 6 and Night 6→7).
     - The reproducibility replicate (Night 9→10).
     - The Day-8 cloud mini-campaign.

     Any session invalidated by a later data-path change is excluded and listed.
  3. Analysis with B = 10,000; Holm-corrected primary tests; static/adaptive comparison; frontier; naturalistic-vs-matched comparison; learning curves.
  4. **Reproducibility bundle:** manifests, hashes, exact commands (`make reproduce-e1` etc.), environment report, validation reports.
  5. Docs: README (what it is, results with CIs, how to reproduce), architecture diagram, demo script, PRODUCT/RESEARCH/IMPLEMENTATION_PLAN updated to match what was built (claims labelled VERIFIED only where measured).
- **Files/modules touched:** `experiments/*`, `docs/**`, `README.md`, `bench/RESULTS.md`, release workflow.
- **Dependencies:** everything above.
- **Tests:** full CI; reproducibility check (re-run one E1 analysis from artifacts: identical numbers); link check on docs.
- **Benchmarks:** final B2/B5 tables per arm and condition.
- **Acceptance criteria:**
  - Every §29 gate passes for all reported results.
  - The report contains E0/E1/E2 with CIs, a static/adaptive comparison, the frontier, and the naturalistic-vs-matched comparison.
  - The reproducibility replicate agrees with E1: the paired Δ balanced-accuracy CI for a0/a1/a3 includes 0, and the cost medians fall within the noise floor. Any disagreement is reported and investigated, not hidden.
  - Release `v1.0.0` with SBOMs and signed images.
- **Failure conditions:**
  - A session is INVALID → report without it (state n) or re-collect if time allows. Never patch data.
  - Results inconclusive → report CIs and resolution limits honestly (a null is a result).
- **Drop if delayed:** the E2 `stream_interval` factor; learning curves beyond D0; cloud re-analysis.
- **End-of-day artifact:** release `v1.0.0` + final report + reproducibility bundle + demo script.
- **Research question answered:** RQ0–RQ2 with frozen evidence.

---

## 31. Post-Day-10 backlog (ordered)

1. P1:
   - D1r and parameter sweeps for the frontier (2–3 points per arm).
   - Simulated-vs-deployed comparison (Whisper-Leak-style trace simulation vs deployed D2/D1r).
   - H1-keepalive and tickets arms; packet-layer attack comparison.
   - MI lower bound; Internet-path client arm (workstation → GCE).
   - L4 passthrough NLB arm.
2. P2:
   - H2-controlled and H2-multiplexed arms (session-level attack).
   - E3 application type (matched only); E4 prefix cache under a passive vantage; E7 netem WAN.
   - GKE Standard deployment; CNN attacker; kNN MI.
3. P3: H3/QUIC pipeline; E5 backend fingerprinting; E8 L4 vs L7; policy optimisation; Maybenot-style machines; NSI packet-mirroring vantage.

## 32. Implementation risks

| # | Risk | Trigger | Mitigation / fallback |
|---|---|---|---|
| I1 | Runtime tail latency at high C | G2/G3 fail at N=256 | Multi-process `SO_REUSEPORT`; cap E2 at validated C; Node/Netty per §3.2 |
| I2 | Parser disagreement with tshark on edge cases | Day-2 validation | Fix before collection; netem fixtures in CI |
| I3 | Local GPU insufficient (6 GB) | Day-1 smoke / Day-6 OOM | 0.5B model locally; move E2 to L4 early |
| I4 | Collection time exceeds nights | Day-3 throughput measurement | Reduce n per class to the power-check minimum; fewer arms in E2 (a0, a1, a3) |
| I5 | Asyncio task GC / cancellation leaks | Soak | Strong task refs, `TaskGroup` per stream, leak tests (fd/task counts) |
| I6 | SDK behaviour changes | Compat suite | Pin SDK versions in tests; re-run on upgrade |
| I7 | vLLM flag/metric churn | Image bump | Pinned digest; manifest records version; upgrade is an explicit PR |
| I8 | WSL2/Docker networking quirks (veth offloads, MTU) | Day-1/2 checks | Lab-check asserts; document; native Linux host as fallback |
| I9 | Cloud quota/cost | Day-1 quota request; budget alerts | Alternate region/GPU; small campaign; stop-on-idle |

---

*End of IMPLEMENTATION_PLAN.md.*
